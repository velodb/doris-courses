# Build only from a clean git archive. Supply reviewed immutable image digests.
ARG TERRAFORM_IMAGE
ARG PYTHON_IMAGE
FROM ${TERRAFORM_IMAGE} AS terraform
FROM terraform AS provider-fetch
COPY workshop/aws/terraform/pilot /module
RUN mkdir /mirror && terraform -chdir=/module providers mirror -platform=linux_amd64 /mirror
FROM ${PYTHON_IMAGE} AS python-fetch
COPY workshop/aws/ttl/wheels.json /wheels.json
RUN python - <<'PYTHON'
import json, pathlib
wheels = json.loads(pathlib.Path('/wheels.json').read_text())['wheels']
pathlib.Path('/wheel-urls.txt').write_text(''.join(row['url'] + ' --hash=sha256:' + row['sha256'] + '\n' for row in wheels))
PYTHON
RUN python -m pip download --no-deps --no-index --require-hashes -r /wheel-urls.txt --dest /wheels
FROM ${PYTHON_IMAGE}
ARG TERRAFORM_IMAGE
ARG PYTHON_IMAGE
RUN case "$TERRAFORM_IMAGE" in *@sha256:*) ;; *) exit 1 ;; esac && \
    case "$PYTHON_IMAGE" in *@sha256:*) ;; *) exit 1 ;; esac
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY --from=provider-fetch /mirror /opt/terraform-providers
WORKDIR /opt/doris-courses
COPY tools/requirements.txt tools/requirements.txt
COPY workshop/aws/ttl/requirements.lock workshop/aws/ttl/requirements.lock
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
RUN --mount=from=python-fetch,source=/wheels,target=/wheels python -m pip --no-cache-dir install --no-index --find-links=/wheels --require-hashes -r workshop/aws/ttl/requirements.lock && \
    python -m pip check && python -m pip uninstall --yes pip && \
    mkdir -p /opt/terraform-providers /opt/doris-workshop /work/tmp && \
    chown -R 10001:10001 /work && chmod 700 /work /work/tmp && chmod -R a+rX /opt/terraform-providers
COPY . /opt/doris-courses
ENV TF_CLI_CONFIG_FILE=/opt/doris-courses/workshop/aws/ttl/terraform.tfrc
RUN TF_DATA_DIR=/tmp/pilot terraform -chdir=workshop/aws/terraform/pilot init -backend=false -input=false -lockfile=readonly && \
    TF_DATA_DIR=/tmp/bindings terraform -chdir=workshop/aws/terraform/access-bindings init -backend=false -input=false -lockfile=readonly && \
    rm -rf /tmp/pilot /tmp/bindings
ARG SOURCE_SHA
RUN python - "$SOURCE_SHA" <<'PY'
import json, pathlib, re, sys
sys.path.insert(0, 'tools')
import workshop_ttl_runner
assert re.fullmatch('[0-9a-f]{40}', sys.argv[1])
pathlib.Path('/opt/doris-workshop/source.json').write_text(json.dumps({'source_sha': sys.argv[1], 'source_tree_sha256': workshop_ttl_runner.tree_hash(pathlib.Path.cwd())}))
PY
ENV HOME=/work TMPDIR=/work/tmp PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true CHECKPOINT_DISABLE=1
LABEL org.opencontainers.image.revision=$SOURCE_SHA
VOLUME ["/work"]
USER 10001:10001
ENTRYPOINT ["python", "-I", "/opt/doris-courses/workshop/aws/ttl/runner_entry.py"]
