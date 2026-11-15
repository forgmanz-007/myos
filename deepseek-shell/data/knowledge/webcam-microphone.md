# Webcam not working
Keywords: webcam, camera, video call, zoom camera, teams camera, uvc, cheese

- Is it detected? `lsusb` (USB webcams) and `ls /dev/video*`.
- Built-in laptop cameras on new Intel laptops (IPU6/IPU7, "MIPI") often have no Linux driver yet.
- Test it in Firefox: https://webcamtests.com (Firefox asks for permission).
- Only one app can use the camera at a time; close other video apps.
- A camera test app (admin): `apt-get install -y kamoso`.
