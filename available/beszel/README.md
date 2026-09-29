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
