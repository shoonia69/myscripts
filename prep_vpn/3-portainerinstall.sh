#!/bin/bash

curl -L https://downloads.portainer.io/ce-sts/portainer-compose.yaml -o portainer-compose.yaml
docker compose -f portainer-compose.yaml up -d