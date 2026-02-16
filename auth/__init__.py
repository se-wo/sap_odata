from abc import ABC, abstractmethod

import requests


class AuthProvider(ABC):
    @abstractmethod
    def authenticate(self, session: requests.Session) -> None:
        """Apply authentication to the session (set headers, cookies, etc.)."""
        ...
