FROM public.ecr.aws/lambda/python:3.12

RUN pip install --upgrade pip

# 시스템 의존성 (pdfplumber, Pillow 등)
RUN dnf install -y gcc gcc-c++ && dnf clean all

# Amazon DocumentDB / RDS CA bundle
RUN curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem \
    -o /etc/pki/ca-trust/source/anchors/rds-global-bundle.pem \
    && update-ca-trust extract

COPY requirements.txt .
RUN pip install -r requirements.txt --target "${LAMBDA_TASK_ROOT}"

COPY app/ ${LAMBDA_TASK_ROOT}/app/
COPY main.py handler.py ${LAMBDA_TASK_ROOT}/

CMD ["handler.handler"]
