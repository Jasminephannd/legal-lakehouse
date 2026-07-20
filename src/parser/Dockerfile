# Build from the repo root (build context needs src/parser AND src/__init__.py):
#   docker buildx build --platform linux/arm64 -f src/parser/Dockerfile -t legal-lakehouse-parser:<git-sha> --load .
#
# arm64 specifically — cheaper and faster per-invocation than x86_64, and
# must match the Lambda function's `architectures = ["arm64"]` in
# infra/main/lambda_function.tf. pyarrow is why this is a container image
# and not a zip: it alone blows past Lambda's 250MB unzipped limit.
FROM public.ecr.aws/lambda/python:3.12

COPY src/parser/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Only what the handler actually imports at runtime — not the ingest
# module (datasets/huggingface_hub aren't needed here and would just
# bloat the image).
COPY src/__init__.py ${LAMBDA_TASK_ROOT}/src/__init__.py
COPY src/parser/ ${LAMBDA_TASK_ROOT}/src/parser/

CMD ["src.parser.handler.lambda_handler"]
