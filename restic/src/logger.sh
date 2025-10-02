#!/bin/bash

while true; do
    filename="log-$(date +'%Y-%m-%d_%H').txt"

    echo "$(date +'%Y-%m-%d %H:%M:%S')" >>"$filename"

    sleep 900 # 15 min
done
