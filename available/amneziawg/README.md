# AmneziaWG Client

Use `amneziawg-client` through Compose `extends`. Each consuming Compose must
place its exported AmneziaWG client profile at:

```text
${CONTAINER_NAME}_data/amneziawg/wg_confs/wg0.conf
```

The profile must contain the complete AWG 3.x client configuration, including
the interface address, keys, endpoint, and protocol parameters. Use an IPv4
address in `Endpoint` so the tunnel can start before DNS is available. Set
`MTU = 1280` in the `[Interface]` section for AWG 3.x.
