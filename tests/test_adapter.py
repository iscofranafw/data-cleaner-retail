import pytest
from olsera_cleaner.api.adapter import parse_page,ResponseFormatError

def test_actual_response_shape():
    page=parse_page({"data":[{"id":125560041,"name":"ORBIT MAX"}],"meta":{"current_page":1,"last_page":1,"total":1,"per_page":"2"},"error":0},1)
    assert page.total==1
    assert page.items[0]["name"]=="ORBIT MAX"

def test_wrong_page_rejected():
    with pytest.raises(ResponseFormatError):
        parse_page({"data":[],"meta":{"current_page":2,"last_page":2,"total":0,"per_page":2}},1)

def test_missing_meta_rejected():
    with pytest.raises(ResponseFormatError):
        parse_page({"data":[]},1)
