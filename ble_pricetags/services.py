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
            success, message = await connect_ble(mac_address)
            if not success:
                _LOGGER.error(f"Failed to connect to device {device_uuid} with MAC address {mac_address}: {message}")
                raise HomeAssistantError(message)
                return
            
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

async def scan_and_find_device(target_mac_addr, timeout=10.0):
    """
    Perform a Bluetooth scan and find a device with the specified MAC address.
    
    Args:
        target_mac_addr (str): The MAC address of the target device.
        timeout (float): The timeout for the scan in seconds.

    Returns:
        target_device (BleakScanner.Device): The found device object, or None if not found.
    """
    try:
        devices = await asyncio.wait_for(BleakScanner.discover(timeout=timeout), timeout=timeout + 10.0)
        _LOGGER.debug(f"Scan complete, found {len(devices)} devices.")
    except asyncio.TimeoutError:
        _LOGGER.error("BLE scan timed out.")
        return None

    if not devices:
        _LOGGER.debug("No devices found.")
        return None

    _LOGGER.debug(f"Found {len(devices)} BLE devices:")
    for device, advertisement_data in devices:
        _LOGGER.debug(f"  - {device.name or 'Unknown'} ({device.address}) RSSI: {advertisement_data.rssi}")

    # Search for the target device by MAC address
    for d in devices:
        if d.address.upper() == target_mac_addr.upper():
            _LOGGER.debug(f"Found target device: {d.name or 'Unknown'} ({d.address})")
            return d

    _LOGGER.warning(f"Target device with MAC address {target_mac_addr} not found.")
    return None

async def connect_ble(target_mac_addr):
    """
    Attempt to connect to a BLE device with the specified MAC address.

    Args:
        target_mac_addr (str): The MAC address of the target device.

    Returns:
        (bool, str): A tuple indicating success or failure and a message.
    """
    _LOGGER.info(f"Starting looking for and connection to {target_mac_addr}...")    
    global ble_client
    max_retries = 10
    
    # 1. Search for the target device
    retry_count = 0
    target_device = None

    while retry_count < max_retries:
        retry_count += 1
        try:
            _LOGGER.debug(f"Scanning for target device (Attempt {retry_count}/{max_retries})...")
            target_device = await scan_and_find_device(target_mac_addr)
            if target_device:
                _LOGGER.info(f"Target device found: {target_device.name or 'Unknown'} ({target_device.address})")
                break
            else:
                _LOGGER.warning(f"Target device not found. Retrying... (Attempt {retry_count}/{max_retries})")
                await asyncio.sleep(2)
        except Exception as e:
            _LOGGER.error(f"Error during scanning: {str(e)}")
            _LOGGER.debug("Detailed exception info:", exc_info=True)

    if not target_device:
        _LOGGER.error(f"Failed to find target device {target_mac_addr} after {max_retries} attempts.")
        return False, f"Failed to find target device after {max_retries} attempts."


    # 2. Connecting to the targeted device
    retry_count = 0
    while retry_count < max_retries:
        retry_count += 1
        ble_client = BleakClient(target_device)
        try:
            _LOGGER.debug(f"Attempting to connect to {target_device.name or 'Unknown'} (Attempt {retry_count}/{max_retries})...")
            await asyncio.wait_for(ble_client.connect(), timeout=15.0)
            _LOGGER.info(f"Connected to {target_device.name or 'Unknown'} ({target_device.address})")

            # Start notifications or perform other operations here
            await ble_client.start_notify(CHARACTERISTIC_CMD_UUID, notification_handler)
            _LOGGER.info(f"Started notifications for {target_device.name or 'Unknown'} ({target_device.address})")
            return True, "Connected successfully"

        except asyncio.TimeoutError:
            _LOGGER.warning(f"Connection to {target_device.address} timed out. Retrying... (Attempt {retry_count}/{max_retries})")
            await asyncio.sleep(2)
        except Exception as e:
            _LOGGER.error(f"Connection failed: {str(e)}")
            _LOGGER.debug("Detailed exception info:", exc_info=True)
        
    _LOGGER.error(f"Failed to connect to target device {target_mac_addr} after {max_retries} attempts.")
    return False, f"Failed to connect to target device after {max_retries} attempts."

  