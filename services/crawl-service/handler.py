from mangum import Mangum
from main import app

handler = Mangum(app, lifespan="auto")
