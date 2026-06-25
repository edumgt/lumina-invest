from mangum import Mangum
from main import app

# Lambda 핸들러 – API Gateway v1/v2 모두 지원
handler = Mangum(app, lifespan="auto", api_gateway_base_path="/api")
