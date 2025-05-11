import logging
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.typing import ConfigType
from .services import async_register_services
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

DOMAIN = "ble_pricetags"
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Set up the custom integration."""

    await async_register_services(hass, DOMAIN)

    async def handle_log_message_service(call: ServiceCall):
        """Handle the log_message service call."""
        # Retrieve the device_id from call.data
        device_ids = call.data.get("device_id", [])
        if not device_ids:
            raise HomeAssistantError("No devices were targeted.")

        # Get the first device_id
        device_id = device_ids[0]

        # Retrieve the device from the Device Registry
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(device_id)
        if not device:
            raise HomeAssistantError(f"Device with ID {device_id} not found.")

        # Log the message and device UUID
        device_uuid = device.id
        message = call.data.get("message", "No message provided")
        _LOGGER.info("Service called: %s | Device UUID: %s", message, device_uuid)

    hass.services.async_register(DOMAIN, "log_message", handle_log_message_service)

    return True

async def async_setup_entry(hass, entry):
    """Set up the integration from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    # Register the device in the Device Registry
    device_registry = dr.async_get(hass)
    mac_address = entry.data["mac_address"]
    screentype = entry.data["screentype"]

    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, mac_address)},
        connections=[("mac", mac_address)],  # Add MAC address to connections
        name=f"BLE Pricetag {mac_address}",
        manufacturer="BLE Pricetags",
        model=screentype,
    )

    return True

async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id)

    # Optionally remove devices from the Device Registry
    device_registry = dr.async_get(hass)
    devices = entry.data.get("devices", [])
    for device in devices:
        mac_address = device["mac_address"]
        device_entry = device_registry.async_get_device(identifiers={(DOMAIN, mac_address)})
        if device_entry:
            device_registry.async_remove_device(device_entry.id)

    return True