from app.utils import load_external_inputs
from app.db import Base, engine

def test_load_seed(tmp_path):
    # ensure DB tables exist on sqlite for tests
    Base.metadata.create_all(bind=engine)
    # use the sample seed file shipped
    count = load_external_inputs()
    assert isinstance(count, int)
