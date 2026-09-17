# Single-node integrated sandbox

Like course 01, the sandbox pins `apache/doris:all-in-one-4.1.3`, runs one FE
and one BE, and retains metadata/storage in named Docker volumes. Unlike course
01, it uses its own Compose project, network, volumes and loopback-only ports.
This is a teaching environment, not a production deployment.

Choose **one** connection mode before starting Jupyter. Merely importing the
helpers does not start Docker; only the explicit D01 preparation step can do so.

From `doris-course/02-data-warehousing`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## A. Use an existing Doris instance

This is the default. Replace the endpoints with your instructor's sandbox.
Do not set DW_START_SANDBOX=yes in this mode.

```bash
export DW_START_SANDBOX=no
export DW_HOST=127.0.0.1
export DW_PORT=9030
export DW_BE_HTTP_URL=http://127.0.0.1:8040
export DW_USER=root
export DW_DATABASE=dw_course_l1_demo
export DW_ALLOW_WRITES=yes
.venv/bin/jupyter lab
```

Set DW_PASSWORD in the process environment if required. Never put credentials
in a notebook. Use trusted endpoints and HTTPS where available.

The helper connects through FE's MySQL protocol. Stream Load deliberately targets
the configured BE HTTP endpoint so it does not forward authentication to a
redirect destination. The BE endpoint must belong to the same cluster.

## B. Start course 02's own Docker sandbox

Start Docker Desktop on macOS or Docker Engine on Linux first. Follow course
01's baseline resource guidance: 4 CPU cores, 8 GB memory and 20 GB free disk.
The new Compose startup path is configuration-checked, but has not been
end-to-end tested on macOS or Linux; report startup failures as unverified setup,
not a successful lab. Do not stop another service to free a port.

```bash
export DW_START_SANDBOX=yes
export DW_HOST=127.0.0.1
export DW_PORT=52030
export DW_BE_HTTP_URL=http://127.0.0.1:51040
export DW_USER=root
export DW_PASSWORD=
export DW_DATABASE=dw_course_l1_demo
export DW_ALLOW_WRITES=yes
.venv/bin/jupyter lab
```

Run D01's **Prepare the single-node environment** cell before connecting.
It validates [compose.yml](compose.yml), starts only project
`doris-warehousing-course`, waits for the image's healthcheck, and checks
`SELECT 1`. First pull/start can take several minutes. The container's internal
ports stay 9030/8030/8040; host ports are 52030/51030/51040. The empty root
password is only for this loopback-bound local sandbox.

For troubleshooting, run these commands from the course root:

```bash
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml ps
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml logs --tail 100 doris
# Stop only this course's container, retaining both volumes.
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml stop
# Start an already-created container without deleting data.
docker compose --project-name doris-warehousing-course --file environments/single-node/compose.yml start --wait
```

Do not delete volumes as a retry mechanism. If ports conflict, use mode A or
have the instructor consistently change Compose ports and DW_* settings;
do not mix the two configurations.

## Reset and verification boundaries

Labs reset only their named tables inside DW_DATABASE. The explicit write flag
acknowledges that scope; it is not a SQL authorization mechanism. No notebook
drops a database, stops services, or changes global configuration. D06 reads
D09-A's clean table and only resets its own D06 tables.

Check the printed FE/BE build information, not only SELECT VERSION(), which can
report a MySQL compatibility version. This single-node environment cannot
demonstrate replica recovery or multi-node isolation. Development-build success
is not a substitute for target-release qualification.
