#!/bin/bash

while true; do
    filename="log-$(date +'%Y-%m-%d_%H').txt"

    echo "$(date +'%Y-%m-%d %H:%M:%S')" >>"$filename"

    sleep 123 # 2 min 3 sec
done
