# Observability with Apache Doris

This directory contains the DOG observability labs and a shared Python runtime
for notebook cells. The runtime follows the same learner-facing pattern as the
real-time analytics course.

## Set up the Python environment

Run these commands from this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Start JupyterLab from the same directory:

```bash
.venv/bin/jupyter lab
```

Every executable notebook finds this course root, imports the stable `DorisLab`
facade (the observability-specific class is also exported as
`ObservabilityLab`), and creates a `lab` object. Once that cell has run, a learner
can execute shell or Doris commands directly from later cells:

```python
lab.shell("docker ps", title="Running containers")
lab.sql("SELECT VERSION() AS doris_version", title="Doris version")
lab.execute("CREATE DATABASE IF NOT EXISTS otel")
```

Module 1 prepares the persistent DOG stack. It clones the upstream stack into
the ignored `.runtime/` directory, mounts the Doris log directories, and starts
Doris, Grafana, and the OpenTelemetry Collector. Module 2 then starts dedicated
collectors for logs, metrics, and traces.

Docker state is kept outside the course files. Re-running an environment cell
reuses existing containers and data instead of replacing them.
