from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from app.web_application import WebApplicationConfigurationError, WebApplicationUsageError
from app.web_application.security import *
from app.web_application.keygen import create_key

KEY = bytes(range(32))
DIGEST = "sha256:" + "a"*64

def test_key_is_exclusive_and_reloads_without_disclosure(tmp_path):
    path=tmp_path/"key.json"
    create_key(output=path)
    key=load_pause_key(path=path)
    assert len(key)==32
    with pytest.raises(WebApplicationConfigurationError):
        create_key(output=path)
    assert load_pause_key(path=path)==key
    for data in ['{}','{"version":"cta-pause-key-v1","key":"secret"}']:
        path.write_text(data)
        with pytest.raises(WebApplicationConfigurationError):
            load_pause_key(path=path)
    with pytest.raises(WebApplicationConfigurationError):
        load_pause_key(path=Path("relative"))

def test_cookie_csrf_and_ip_normalization():
    cookie=new_cookie()
    assert len(cookie)==43
    visitor=cookie_hash(cookie)
    assert check_csrf(KEY,visitor,csrf_token(KEY,visitor))
    assert not check_csrf(KEY,cookie_hash(new_cookie()),csrf_token(KEY,visitor))
    assert not check_csrf(KEY,visitor,"wrong")
    assert ip_hash("2001:0db8::1")==ip_hash("2001:db8::1")
    for raw in ["invalid","fe80::1%eth0"]:
        with pytest.raises(WebApplicationUsageError):
            ip_hash(raw)
    with pytest.raises(WebApplicationUsageError):
        cookie_hash("x"*43)

def test_aes_binds_run_generation_digest_and_key():
    run=uuid4()
    args=dict(run_id=run,pause_generation=2,contract_digest=DIGEST)
    nonce,ciphertext=encrypt_pause(KEY,**args,pause_token="server-only-token")
    assert b"server-only-token" not in ciphertext
    assert decrypt_pause(KEY,**args,nonce=nonce,ciphertext=ciphertext)=="server-only-token"
    assert encrypt_pause(KEY,**args,pause_token="server-only-token")[0]!=nonce
    for changes in [dict(run_id=uuid4()),dict(pause_generation=3),dict(contract_digest="sha256:"+"b"*64),
                    dict(nonce=bytes(12)),dict(ciphertext=ciphertext[:-1])]:
        with pytest.raises(WebApplicationUsageError):
            decrypt_pause(KEY,**(args|dict(nonce=nonce,ciphertext=ciphertext)|changes))
    with pytest.raises(WebApplicationUsageError):
        decrypt_pause(bytes(32),**args,nonce=nonce,ciphertext=ciphertext)

def test_separate_derivation_fingerprint_and_authenticated_cursor():
    visitor=cookie_hash(new_cookie())
    assert derive(KEY,"csrf-v1")!=derive(KEY,"request-v1")!=KEY
    args=dict(route="start",run_id=None,generation=None,body={"a":"secret","b":1})
    fingerprint=body_fingerprint(KEY,**args)
    assert fingerprint == body_fingerprint(KEY,**(args|{"body":{"b":1,"a":"secret"}}))
    assert fingerprint != body_fingerprint(KEY,**(args|{"route":"recover"}))
    when,run=datetime(2026,10,5,tzinfo=timezone.utc),uuid4()
    cursor=make_cursor(KEY,visitor_hash=visitor,bound_at=when,run_id=run)
    assert len(cursor)<=512 and read_cursor(KEY,cursor=cursor,visitor_hash=visitor)==(when,run)
    for changes in [dict(cursor=cursor[:-1]+"!"),dict(visitor_hash="b"*64),dict(cursor="x"*513)]:
        with pytest.raises(WebApplicationUsageError):
            read_cursor(KEY,**(dict(cursor=cursor,visitor_hash=visitor)|changes))
