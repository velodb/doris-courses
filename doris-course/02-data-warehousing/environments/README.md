# Lab environment

Use a dedicated single-FE/single-BE integrated-storage sandbox. The first PR
does not create containers or change the existing real-time analytics runtime.
You can reuse that course's running sandbox with a **different database**,
or ask the instructor for a disposable Doris environment.

Target release for recording: Doris 4.1.3, matching the existing course.
A development build is not a substitute for release qualification.

From `doris-course/02-data-warehousing`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export DW_HOST=127.0.0.1
export DW_PORT=9030
export DW_BE_HTTP_URL=http://127.0.0.1:8040
export DW_USER=root
export DW_DATABASE=dw_course_l1_demo
export DW_ALLOW_WRITES=yes
.venv/bin/jupyter lab
```

Set DW_PASSWORD in the process environment if required. Do not paste credentials
into notebooks or commit an environment file. Root with an empty password is
only the existing local sandbox convention, not a production recommendation.
Use trusted endpoints and HTTPS where available.

The helper connects through FE's MySQL protocol. Stream Load deliberately targets
the configured BE HTTP endpoint so it does not forward authentication to a
redirect destination. The BE endpoint must belong to the same cluster.

Labs reset only their named tables inside DW_DATABASE. The explicit write flag
acknowledges that scope; it is not a SQL authorization mechanism. No notebook
drops a database, stops services, or changes global configuration. D06 reads
D09-A's clean table and only resets its own D06 tables.

Check the printed FE/BE build information, not only SELECT VERSION(), which can
report a MySQL compatibility version. This single-node environment cannot
demonstrate replica recovery or multi-node isolation.
