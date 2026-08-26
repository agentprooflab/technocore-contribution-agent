from tca.publisher import _receipt_from_url


def test_stored_technocore_receipt_is_recoverable_without_reposting() -> None:
    receipt = _receipt_from_url(
        "https://technocore.chat/humans#r/technocore/123?nonce=456",
        "hello",
        "did:key:z6MkExample",
    )
    assert receipt == {
        "room": "technocore",
        "seq": 123,
        "nonce": 456,
        "from": "did:key:z6MkExample",
        "text": "hello",
    }
