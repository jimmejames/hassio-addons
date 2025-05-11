import logging
import os
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

import asyncio
import json
import struct
import time
from PIL import Image
from bleak import BleakClient, BleakScanner

_LOGGER = logging.getLogger(__name__)


#################
CHARACTERISTIC_CMD_UUID = "0000fef1-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_IMG_UUID = "0000fef2-0000-1000-8000-00805f9b34fb"

canvas_width = 400
canvas_height = 300
ble_client = None
img_data = b""
image_part_size = 180
upload_done = False
#################

async def async_register_services(hass: HomeAssistant, domain: str):
    """Register services for the integration."""

    async def handle_upload_image_service(call: ServiceCall):
        """Handle the send_image service call."""
        
        # Handle image file
        image_file = call.data.get("image_file")
        if not image_file or not os.path.isfile(image_file):
            raise HomeAssistantError(f"Invalid or missing image file: {image_file}")

        # Retrieve devices from the target field
        device_registry = dr.async_get(hass)
        device_ids = call.data.get("device_id", [])
        if not device_ids:
            raise HomeAssistantError("No devices were targeted.")

        # Iterate through all devices in the device_ids list
        for device_id in device_ids:
            device = device_registry.async_get(device_id)
            if not device:
                _LOGGER.warning(f"Device with ID {device_id} not found. Skipping...")
                continue

            device_uuid = device.id

            # Log the event
            _LOGGER.info("Image received for device %s | File: %s", device_uuid, image_file)
            
            # Extract the MAC address from the connections
            mac_address = next(
                (connection[1] for connection in device.connections if connection[0] == "mac"),
                None
            )

            if mac_address:
                _LOGGER.info(f"MAC Address for device {device_id}: {mac_address}")
            else:
                _LOGGER.warning(f"No MAC Address found for device {device_id}.")


            _LOGGER.info(f"Connecting to device {device_uuid} with MAC address {mac_address}...")

            # So, finally let's connect to the device
            global prepared_image
            prepared_image = verify_and_scale_image(image_file)

            await connect_ble(mac_address)
            _LOGGER.info(f"Uploading image ({image_file}) to device {device_uuid} with MAC address {mac_address}...")
            await upload_image()

            global upload_done
            while not upload_done:          
                await asyncio.sleep(0.2)
            _LOGGER.info(f"Everything is awsome.")                

    # Regisztráljuk a send_image service-t
    hass.services.async_register(domain, "upload_image", handle_upload_image_service)


# Custom part from the original code 
# by @jimmejames
# based on https://atc1441.github.io/ATC_GICISKY_Paper_Image_Upload.html

async def upload_image():
    global img_data
    img_data = bytes(get_bitpacked_image_data(prepared_image))
    _LOGGER.debug(f"Total bytes to send: {len(img_data)}")

    await send_command("01")
    await send_command("02" + format_le_uint32(len(img_data)) + "000000")

def format_le_uint32(value):
    return ''.join(f"{b:02x}" for b in struct.pack("<I", value))

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

def verify_and_scale_image(image_path):
    _LOGGER.debug(f"Loading image: {image_path}")
    image = Image.open(image_path)
    if image.size != (canvas_width, canvas_height):
        _LOGGER.debug(f"Original image size: {image.size}. Scaling to ({canvas_width}, {canvas_height})...")
        image = image.resize((canvas_width, canvas_height))
        _LOGGER.debug("Scaling complete.")
    else:
        _LOGGER.debug("Image already matches target dimensions.")
    return image

def notification_handler(sender, data):
    global image_part_size, upload_done
    hex_data = data.hex()
    _LOGGER.debug(f"Got bytes: {hex_data}")
    if hex_data.startswith("01"):
        size = int.from_bytes(data[1:3], byteorder="little")
        image_part_size = size - 4
        _LOGGER.debug(f"Display requested part size: {image_part_size}")
    elif hex_data.startswith("02"):
        asyncio.run_coroutine_threadsafe(send_command("03"), asyncio.get_event_loop())
    elif hex_data.startswith("05"):
        if data[1] == 0x08:
            _LOGGER.debug("Upload complete. Disconnecting.")
            upload_done = True
            asyncio.run_coroutine_threadsafe(ble_client.disconnect(), asyncio.get_event_loop())
        elif data[1] != 0x00:
            _LOGGER.debug("Display reported an error during upload.")
        else:
            ack_part = struct.unpack("<I", data[2:6])[0]
            asyncio.run_coroutine_threadsafe(send_next_image_part(ack_part), asyncio.get_event_loop())

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
        _LOGGER.warning(f"Error sending part {part_number}: {e}")

async def send_command(cmd_hex):
    if ble_client and ble_client.is_connected:
        data = bytes.fromhex(cmd_hex)
        await ble_client.write_gatt_char(CHARACTERISTIC_CMD_UUID, data)

async def connect_ble(target_mac_addr):
    
    global ble_client
    max_retries = 10
    retry_count = 0
    while True:
        try:
            retry_count += 1
            if retry_count > max_retries:
                _LOGGER.error("Max retries reached. Exiting...")
                break

            _LOGGER.info(f"Starting connection to {target_mac_addr}...")
            _LOGGER.debug(f"Starting BLE scan for {target_mac_addr}...")
            devices = await asyncio.wait_for(BleakScanner.discover(timeout=5.0), timeout=8.0)
            _LOGGER.debug(f"Scan complete, found {len(devices)} devices.")  # Log the number of devices found

            if not devices:
                _LOGGER.debug("No devices found.")
            else:
                _LOGGER.debug(f"Found {len(devices)} BLE devices:")
                for d in devices:
                    _LOGGER.debug(f"  - {d.name or 'Unknown'} ({d.address}) RSSI: {d.rssi}")

            target_device = None
            for d in devices:
                if d.address.upper() == target_mac_addr.upper():
                    target_device = d
                    break

            if target_device:
                _LOGGER.debug(f"Found target device: {target_device.name or 'Unknown'} ({target_device.address})")

                ble_client = BleakClient(target_device)
                try:
                    _LOGGER.debug(f"Attempting to connect to {target_device.name or 'Unknown'}...")
                    await ble_client.connect()
                    _LOGGER.debug(f"Connected to {target_device.name or 'Unknown'} ({target_device.address})")
                except Exception as e:
                    _LOGGER.error(f"Connection failed: {e}")
                    continue

                await ble_client.start_notify(CHARACTERISTIC_CMD_UUID, notification_handler)
                _LOGGER.info(f"Started notifications for {target_device.name or 'Unknown'} ({target_device.address})")
                return  # Successfully connected, exit the loop

            else:
                _LOGGER.warning("Target device not found. Waiting 3 seconds before retrying...")
                await asyncio.sleep(3)

        except Exception as e:
            _LOGGER.error("Scan failed with exception:")
            _LOGGER.error(str(e))
            await asyncio.sleep(5)