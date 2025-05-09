import asyncio
import json
import os
import struct
import time
from PIL import Image
from bleak import BleakClient, BleakScanner

SERVICE_UUID = "0000fef0-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_CMD_UUID = "0000fef1-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_IMG_UUID = "0000fef2-0000-1000-8000-00805f9b34fb"

os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=/var/run/dbus/system_bus_socket"

#################
CONFIG_PATH = "/data/options.json"  # Configuration path for Home Assistant

canvas_width = 400
canvas_height = 300
ble_client = None
img_data = b""
image_part_size = 180
upload_done = False
#################

def log(msg):
    print(msg)

def get_bitpacked_image_data(image):
    image = image.convert("RGB")
    pixels = image.load()
    byte_data = []
    byte_data_red = []
    bit_position = 7
    current_byte = 0
    current_byte_red = 0

    for y in range(canvas_height):
        for x in range(canvas_width):
            r, g, b = pixels[x, y]
            luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if luminance > 128:
                current_byte |= (1 << bit_position)
            if r > 170 and g < 170:
                current_byte_red |= (1 << bit_position)
            bit_position -= 1
            if bit_position < 0:
                byte_data.append(current_byte)
                byte_data_red.append(current_byte_red)
                current_byte = 0
                current_byte_red = 0
                bit_position = 7

    if bit_position != 7:
        byte_data.append(current_byte)
        byte_data_red.append(current_byte_red)

    return byte_data + byte_data_red

def notification_handler(sender, data):
    global image_part_size, upload_done
    hex_data = data.hex()
    log(f"Got bytes: {hex_data}")
    if hex_data.startswith("01"):
        size = int.from_bytes(data[1:3], byteorder="little")
        image_part_size = size - 4
        log(f"Display requested part size: {image_part_size}")
    elif hex_data.startswith("02"):
        asyncio.run_coroutine_threadsafe(send_command("03"), asyncio.get_event_loop())
    elif hex_data.startswith("05"):
        if data[1] == 0x08:
            log("Upload complete. Disconnecting.")
            upload_done = True
            asyncio.run_coroutine_threadsafe(ble_client.disconnect(), asyncio.get_event_loop())
        elif data[1] != 0x00:
            log("Display reported an error during upload.")
        else:
            ack_part = struct.unpack("<I", data[2:6])[0]
            asyncio.run_coroutine_threadsafe(send_next_image_part(ack_part), asyncio.get_event_loop())

async def connect_ble(target_mac_addr):
    global ble_client
    while True:
        try:
            log(f"Starting BLE scan for {target_mac_addr}...")
            devices = await asyncio.wait_for(BleakScanner.discover(timeout=5.0), timeout=8.0)
            log(f"Scan complete, found {len(devices)} devices.")  # Log the number of devices found

            if not devices:
                log("No devices found.")
            else:
                log(f"Found {len(devices)} BLE devices:")
                for d in devices:
                    log(f"  - {d.name or 'Unknown'} ({d.address}) RSSI: {d.rssi}")

            target_device = None
            for d in devices:
                if d.address.upper() == target_mac_addr.upper():
                    target_device = d
                    break

            if target_device:
                log(f"Found target device: {target_device.name or 'Unknown'} ({target_device.address})")

                ble_client = BleakClient(target_device)
                try:
                    log(f"Attempting to connect to {target_device.name or 'Unknown'}...")
                    await ble_client.connect()
                    log(f"Connected to {target_device.name or 'Unknown'} ({target_device.address})")
                except Exception as e:
                    log(f"Connection failed: {e}")
                    continue

                await ble_client.start_notify(CHARACTERISTIC_CMD_UUID, notification_handler)
                log(f"Started notifications for {target_device.name or 'Unknown'} ({target_device.address})")
                return  # Successfully connected, exit the loop

            else:
                log("Target device not found. Waiting 3 seconds before retrying...")
                await asyncio.sleep(3)

        except Exception as e:
            log("Scan failed with exception:")
            log(str(e))
            await asyncio.sleep(5)


async def send_command(cmd_hex):
    if ble_client and ble_client.is_connected:
        data = bytes.fromhex(cmd_hex)
        await ble_client.write_gatt_char(CHARACTERISTIC_CMD_UUID, data)

async def send_next_image_part(part_number):
    global img_data
    start = part_number * image_part_size
    end = start + image_part_size
    chunk = img_data[start:end]
    if not chunk:
        return
    header = struct.pack("<I", part_number)
    full_packet = header + chunk
    try:
        await ble_client.write_gatt_char(CHARACTERISTIC_IMG_UUID, full_packet)
    except Exception as e:
        log(f"Error sending part {part_number}: {e}")

async def upload_image():
    global img_data
    img_data = bytes(get_bitpacked_image_data(prepared_image))
    log(f"Total bytes to send: {len(img_data)}")

    await send_command("01")
    await send_command("02" + format_le_uint32(len(img_data)) + "000000")

def format_le_uint32(value):
    return ''.join(f"{b:02x}" for b in struct.pack("<I", value))

def verify_and_scale_image(image_path):
    log(f"Loading image: {image_path}")
    image = Image.open(image_path)
    if image.size != (canvas_width, canvas_height):
        log(f"Original image size: {image.size}. Scaling to ({canvas_width}, {canvas_height})...")
        image = image.resize((canvas_width, canvas_height))
        log("Scaling complete.")
    else:
        log("Image already matches target dimensions.")
    return image

async def main(image_path, target_mac):
    global prepared_image
    prepared_image = verify_and_scale_image(image_path)

    await connect_ble(target_mac)
    await upload_image()

    global upload_done
    while not upload_done:
        await asyncio.sleep(0.2)

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

if __name__ == "__main__":
    # The configuration is passed from the `run.sh` script via the environment variables
    config = load_config()
    TARGET_ADDRESS = config.get("mac_address")
    IMAGE_FILENAME = config.get("image_filename")
    IMAGE_PATH = f"/config/images/{IMAGE_FILENAME}"

    if not os.path.isfile(IMAGE_PATH):
        log(f"Image file not found: {IMAGE_PATH}")
        exit(1)

    asyncio.run(main(IMAGE_PATH, TARGET_ADDRESS))
