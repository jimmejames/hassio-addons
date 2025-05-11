from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr
from homeassistant.exceptions import HomeAssistantError

import voluptuous as vol
import re
import logging
_LOGGER = logging.getLogger(__name__)

DOMAIN = "ble_pricetags"

# List of available screen types
SCREEN_TYPES = ["4,2 BWR"]

# Regex for validating MAC address
MAC_REGEX = r"^([0-9A-Fa-f]{2}:){5}([0-9A-Fa-f]{2})$"

class BlePricetagsConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BLE Pricetags."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self.screentype = None
        self.mac_address = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            screentype = user_input.get("screentype")
            mac_address = user_input.get("mac_address")

            # Validate MAC address
            if not re.match(MAC_REGEX, mac_address):
                errors["mac_address"] = "invalid_mac"

            if not errors:
                # Create the configuration entry
                return self.async_create_entry(
                    title=f"Device {mac_address}",
                    data={
                        "screentype": screentype,
                        "mac_address": mac_address,
                    },
                )

        # Show the form to the user
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("screentype"): vol.In(SCREEN_TYPES),
                    vol.Required("mac_address"): str,
                }
            ),
            errors=errors,
        )

    async def _create_device(self, mac_address, screentype):
        """Create a device in the Home Assistant Device Registry."""
        device_registry = dr.async_get(self.hass)
    
        # Ensure the config_entry_id is valid
        config_entry_id = self.context.get("entry_id")
        if not config_entry_id:
            raise HomeAssistantError("Invalid config_entry_id. Cannot create device.")
        _LOGGER.debug("Config Entry ID: %s", config_entry_id)


        # Add the device to the registry
        # Igazából mint ha az __init__-ben lenne, de nem tudom hogy itt miért nem működik
        device = device_registry.async_get_or_create(
            config_entry_id=self.context.get("entry_id"),
            identifiers={(DOMAIN, mac_address)},  # Unique identifier for the device
            connections=[("mac", mac_address)],  # Add MAC address to connections
            name=f"BLE Pricetag {mac_address}",
            manufacturer="BLE Pricetags",
            model=screentype,            
        )

        _LOGGER.debug(f"Device created: {device}")