from .config import from_env
from .main import create_app

app = create_app(from_env())
