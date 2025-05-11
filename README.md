# BLE Pricetags - Home Assistant Custom Integration

This is a custom integration for Home Assistant that enables support for Bluetooth Low Energy (BLE) price tags. It allows you to monitor and interact with BLE-enabled price tags directly from your Home Assistant setup.

## Features

- Connect to BLE price tags.
- Uploads images to these tags.

## Installation

1. Download the `ble_pricetags` folder and place it in your Home Assistant `config/custom_components` directory.
2. Restart Home Assistant.
3. Go to **Settings** > **Devices & Services** > **Integrations** and click on **Add Integration**.
4. You need to choose screentype and provide the MAC address of the BLE price tag.    

## Usage
1. Upload an image to your Home Assistant `config` directory or any accessible folder, for example, `/config/images/`.
2. Use the `ble_pricetags.upload_image` service to send an image to the connected BLE price tags. You can call this service from the **Developer Tools** > **Services** section in Home Assistant.

Here is an example YAML for calling the service with a target and data:

```yaml
service: ble_pricetags.upload_image
target:
    device_id: 54240410e64f2c0b901e1552d0ca9184
data:
    image_file: "/config/images/image.png"
```

## Limitations
- This integration is still in its early stages of development.
- Currently, it only works with 4.2'' displays.

## Info
Based on https://atc1441.github.io/ATC_GICISKY_Paper_Image_Upload.html
