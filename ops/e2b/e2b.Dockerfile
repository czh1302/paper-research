# Pin the E2B base image index so scientific reruns do not silently inherit a
# different operating-system environment. Refresh deliberately with a new
# template version after validating the replacement image.
FROM e2bdev/base@sha256:4a369f01a820fe5e65f53c2c5727a78899daf86f0541b721097f289559c8b73f

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       build-essential ca-certificates curl git jq tmux unzip zip \
    && rm -rf /var/lib/apt/lists/*

USER user
RUN python3 -m pip install --user --no-cache-dir \
      jsonschema==4.25.1 pytest==8.4.2 ruff==0.12.12

WORKDIR /home/user/repository
