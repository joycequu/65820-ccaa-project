# 65820-ccaa-project

## Table of Contents

- [Video Content Instructions](#validate-content-creation)

## Video Content Instructions

### Create DASH Content

Save desired video as an .mp4 and place it into root directory. In `create_dash_content.sh`, update `SOURCE_FILE` variable to file name. Then, run

```console
./create_dash_content.sh
```

_Note: You may need to use `chmod +x` to make `create_dash_content.sh` executable._

### Validate Content Creation

Ensure Docker Desktop is running. In `DASH_content`, run

```console
docker run --name tcp_server_cubic -d \
  --cap-add=NET_ADMIN \
  --sysctl net.ipv4.tcp_congestion_control=cubic \
  -p 8080:80 \
  -v "$(pwd)":/usr/share/nginx/html:ro \
  -v "$(pwd)/default.conf":/etc/nginx/conf.d/default.conf:ro \
  nginx:latest
```

Navigate to a dash.js reference player (such as [here](https://reference.dashif.org/dash.js/nightly/samples/dash-if-reference-player/index.html)).
Load http://localhost:8080/manifest.mpd and your video should start playing.
