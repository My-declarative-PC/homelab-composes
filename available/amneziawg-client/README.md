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

Each concurrently running sidecar needs a separate peer and profile. Do not
reuse a private key or tunnel address across Jellyfin, Audiobookshelf,
RSS-to-Telegram, and qBittorrent.

| Service | Profile path |
| --- | --- |
| Jellyfin | `jellifin_data/amneziawg/wg_confs/wg0.conf` |
| Audiobookshelf | `audiobookshelf_data/amneziawg/wg_confs/wg0.conf` |
| RSS-to-Telegram | `rsstt_data/amneziawg/wg_confs/wg0.conf` |
| qBittorrent VPN | `qbittorrent_data/amneziawg/wg_confs/wg0.conf` |

Create the profile before starting its sidecar. Without `wg0.conf`, the
sidecar is unhealthy and Compose will not start the dependent application.

To keep a LAN reachable through a published container port, add the following
to `[Interface]`, substituting the local subnet:

```ini
PostUp = ip rule add to 192.168.10.0/24 table main priority 100
PostDown = ip rule del to 192.168.10.0/24 table main priority 100
```

qBittorrent peer-port publishing only exposes the port on the Docker host. To
accept inbound peers over the VPN, configure matching port forwarding on the
AmneziaWG server.
