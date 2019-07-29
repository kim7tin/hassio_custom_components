from requests import get

DOMAIN = 'fbm_pusher'


def setup(hass, config):
    """Set up is called when Home Assistant is loading our component."""
    tm_key = config[DOMAIN]['tm_key']
    def handle_send(call):
        """Handle the service call."""
        message = call.data.get("message", "K%C3%AAnh%20T%C3%A1y%20M%C3%A1y%20-%20Xin%20ch%C3%A0o%20b%E1%BA%A1n!%0AERR%3A%20Kh%C3%B4ng%20c%C3%B3%20n%E1%BB%99i%20dung%20truy%E1%BB%81n%20v%C3%A0o!")
        response = get('https://taymay.herokuapp.com/send/?key={}&message={}'.format(tm_key, message))
        print(response.text)

    hass.services.register(DOMAIN, 'send', handle_send)
    return True