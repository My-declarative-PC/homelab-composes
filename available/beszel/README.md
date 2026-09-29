# Beszel

## Intel GPU

The agent maps `/dev/dri` and has `CAP_PERFMON` for Intel GPU monitoring with the `i915` driver. The default PID namespace is private.

For an Intel GPU using the `xe` driver, set the following in the local `.env` file to enable GPU utilization metrics:

```dotenv
BESZEL_AGENT_PID_MODE=host
```

The host PID namespace is not required for `i915`. To identify the driver on the host, run:

```sh
readlink -f /sys/class/drm/card*/device/driver
```

The final path component is the driver name (`i915` or `xe`).

## Docker API and SELinux

The agent accesses Docker through `beszel-docker-socket-proxy` instead of mounting the Docker socket directly. The proxy only enables container endpoints and read-only HTTP methods; log reads are enabled for Beszel's container details view. Its port is published on host loopback only because the agent uses host networking.

The Docker socket mount uses the shared SELinux label (`:z`), and the agent retains its normal container label. Do not add `label:disable` to the agent. On the host, verify that SELinux is enforcing and that Docker is using SELinux labels; if access is denied, inspect the SELinux audit log and add a narrowly scoped policy rather than disabling labels for the agent.
