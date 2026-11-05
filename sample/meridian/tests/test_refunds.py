from services.refunds import eligible

def test_refund_within_window():
    assert eligible(14)

def test_refund_after_window():
    assert not eligible(15)
