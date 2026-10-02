from tailrace import rules

OB = {"id": "po-1", "attempts": 1, "receiver": "old@example.com"}
PAYEE = {"name": "Priya", "verified_email": "good@example.com"}


def v(action, address=None, reasoning="PayPal reported the email has no account, so use the verified one.", ob=OB, payee=PAYEE, err="RECEIVER_UNREGISTERED"):
    return rules.validate(action, address, reasoning, ob, payee, {"error_name": err})


def test_correct_to_the_verified_address_passes():
    assert v("correct", "good@example.com") == []


def test_correct_to_an_address_not_in_the_directory_is_refused():
    assert any("not the verified address" in e for e in v("correct", "invented@example.com"))


def test_correct_with_no_verified_address_on_file_is_refused():
    assert any("no verified address" in e for e in v("correct", "x@example.com", payee={"name": "Dana", "verified_email": None}))


def test_correct_to_the_address_that_just_failed_is_refused():
    assert any("just failed" in e for e in v("correct", "old@example.com"))


def test_retry_only_for_transient_errors():
    assert v("retry", err="INTERNAL_SERVICE_ERROR") == []
    assert any("only allowed for transient" in e for e in v("retry", err="RECEIVER_UNREGISTERED"))


def test_self_pay_cannot_be_corrected_around():
    assert any("payee record is wrong" in e for e in v("correct", "good@example.com", err="SELF_PAY_NOT_ALLOWED"))


def test_after_three_attempts_only_escalate_or_stop():
    ob = dict(OB, attempts=3)
    assert any("already used 3" in e for e in v("correct", "good@example.com", ob=ob))
    assert v("escalate", ob=ob) == [] and v("stop", ob=ob) == []


def test_reasoning_is_required():
    assert any("reason" in e for e in v("escalate", reasoning="ok"))


def test_unknown_action_is_refused():
    assert v("refund") == ["action must be one of retry, correct, escalate, stop"]
