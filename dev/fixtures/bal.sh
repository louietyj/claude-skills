#!/bin/bash
# prints both solver balances
C=$(tr -d '\r\n' < /mnt/skills/user/headless-browser/capsolver.key); T=$(tr -d '\r\n' < /mnt/skills/user/headless-browser/2captcha.key)
echo "capsolver=$(curl -s https://api.capsolver.com/getBalance -H 'Content-Type: application/json' -d "{\"clientKey\":\"$C\"}" | grep -oE '"balance":[0-9.]+' | cut -d: -f2) 2captcha=$(curl -s https://api.2captcha.com/getBalance -H 'Content-Type: application/json' -d "{\"clientKey\":\"$T\"}" | grep -oE '"balance":[0-9.]+' | cut -d: -f2)"
