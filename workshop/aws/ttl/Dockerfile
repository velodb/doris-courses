# Build only from a clean git archive. Supply reviewed immutable image digests.
ARG TERRAFORM_IMAGE
ARG PYTHON_IMAGE
FROM ${TERRAFORM_IMAGE} AS terraform
FROM ${PYTHON_IMAGE}
ARG TERRAFORM_IMAGE
ARG PYTHON_IMAGE
ARG SOURCE_SHA
RUN case "$TERRAFORM_IMAGE" in *@sha256:*) ;; *) exit 1 ;; esac && \
    case "$PYTHON_IMAGE" in *@sha256:*) ;; *) exit 1 ;; esac
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY . /opt/doris-courses
WORKDIR /opt/doris-courses
RUN python -m pip --no-cache-dir install -r workshop/aws/preflight-requirements.txt && \
    mkdir -p /opt/terraform-cache /opt/doris-workshop /work && chown 10001:10001 /work
ENV TF_PLUGIN_CACHE_DIR=/opt/terraform-cache
RUN terraform -chdir=workshop/aws/terraform/pilot init -backend=false -input=false -lockfile=readonly && \
    terraform -chdir=workshop/aws/terraform/access-bindings init -backend=false -input=false -lockfile=readonly
RUN python - "$SOURCE_SHA" <<'PY'
import json, pathlib, re, sys
sys.path.insert(0, 'tools')
import workshop_ttl_runner
assert re.fullmatch('[0-9a-f]{40}', sys.argv[1])
pathlib.Path('/opt/doris-workshop/source.json').write_text(json.dumps({'source_sha': sys.argv[1], 'source_tree_sha256': workshop_ttl_runner.tree_hash(pathlib.Path.cwd())}))
PY
ENV HOME=/work PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 AWS_EC2_METADATA_DISABLED=true
VOLUME ["/work"]
USER 10001:10001
ENTRYPOINT ["python", "-I", "/opt/doris-courses/workshop/aws/ttl/runner_entry.py"]
