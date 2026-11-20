# Security and privacy

The integration sends commands through Home Assistant's core Roborock integration and stores only the vacuum's entity-registry id and an optional RTSP address. It holds no Roborock credentials and no remote-video pattern.

The `roborock://` source in your go2rtc configuration contains Roborock cloud credentials and the pattern. Keep that configuration private, do not expose go2rtc's API or web UI outside your network, and remember that its API returns stream sources in clear text.

Remote drive, pet search and patrol move the robot, and the camera and talk features see and speak in your home. The services and buttons require control permission on the vacuum; give that permission only to people who may do this. The robot may announce remote viewing.

Do not attach credentials, stream URLs, device ids, map data, recordings or unredacted logs to issues. Report vulnerabilities through GitHub's private vulnerability reporting.
