# payments/telr.py
import requests
from django.conf import settings

TELR_ENDPOINT = "https://secure.telr.com/gateway/order.json"

def _telr_test_flag() -> str:
    # Telr يتوقع "1" أو "0"
    return "1" if bool(getattr(settings, "TELR_TEST_MODE", False)) else "0"

def generate_telr_url(purchase, request, cart_id: str):
    package = purchase.package

    amount = str(getattr(package, "effective_price", None) or package.discounted_price or package.price)
    game_type = package.game_type

    base_url = getattr(settings, "TELR_BASE_URL", "https://wesh-aljawab.com").rstrip("/")

    return_auth = f"{base_url}/payments/telr/success/?purchase={purchase.id}&type={game_type}&cartid={cart_id}"
    return_decl = f"{base_url}/payments/telr/failed/?purchase={purchase.id}&type={game_type}&cartid={cart_id}"
    return_can  = f"{base_url}/payments/telr/cancel/?purchase={purchase.id}&type={game_type}&cartid={cart_id}"

    notify_url  = f"{base_url}/payments/telr/webhook/"

    payload = {
        "ivp_method": "create",
        "ivp_store": getattr(settings, "TELR_STORE_ID"),
        "ivp_authkey": getattr(settings, "TELR_AUTH_KEY"),
        "ivp_test": _telr_test_flag(),

        "ivp_cart": cart_id,
        "ivp_amount": amount,
        "ivp_currency": "SAR",
        "ivp_desc": f"حزمة رقم {package.package_number}",
        "ivp_lang": "ar",

        "return_auth": return_auth,
        "return_decl": return_decl,
        "return_can": return_can,
        "ivp_callback": notify_url,

        "bill_email": request.user.email or "noemail@wesh-aljawab.com",
        "bill_fname": request.user.first_name or request.user.username,
    }

    return TELR_ENDPOINT, payload

def telr_check(cart_id: str) -> dict:
    """
    يتحقق من حالة الطلب من Telr.
    cart_id يجب أن يكون الـ ref اللي Telr رجعه، مو local-xxx
    """
    data = {
        "ivp_method": "check",
        "ivp_store": getattr(settings, "TELR_STORE_ID"),
        "ivp_authkey": getattr(settings, "TELR_AUTH_KEY"),
        "ivp_test": _telr_test_flag(),
        "order_ref": cart_id,  # ✅ استخدام order_ref بدل ivp_cart
    }
    r = requests.post(TELR_ENDPOINT, data=data, timeout=20)
    r.raise_for_status()
    return r.json()
