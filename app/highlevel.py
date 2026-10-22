import os
import httpx
from .db import DemoAppointment

class CRMError(Exception):
    pass

class HighLevel:
    def __init__(self, client, db):
        self.client, self.db = client, db

    def _get(self, path):
        token = os.getenv('HIGHLEVEL_TOKEN', '')
        if not token:
            raise CRMError('HighLevel token missing; no calendar coverage')
        # HighLevel has endpoint-specific API versions. Override in .env if needed.
        headers = {'Authorization': f'Bearer {token}', 'Version': os.getenv('HIGHLEVEL_VERSION', 'v3'), 'Accept': 'application/json'}
        try:
            resp = httpx.get('https://services.leadconnectorhq.com' + path, headers=headers, timeout=12.0)
        except httpx.HTTPError as exc:
            raise CRMError(f'HighLevel network error: {type(exc).__name__}') from exc
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise CRMError(f'HighLevel HTTP {resp.status_code} (auth/scope/version/availability)')
        try:
            return resp.json()
        except ValueError as exc:
            raise CRMError('Invalid HighLevel JSON') from exc

    def by_id(self, appointment_id):
        if self.client.is_demo:
            row = self.db.get(DemoAppointment, appointment_id)
            return row.record if row and row.client_id == self.client.id else None
        # Route is documented. IDs must come from actual Retell tool events.
        return (self._get(f'/calendars/events/appointments/{appointment_id}') or {}).get('event')

    def by_contact(self, contact_id):
        if self.client.is_demo:
            return [r.record for r in self.db.query(DemoAppointment).filter_by(client_id=self.client.id).all() if r.record.get('contactId') == contact_id]
        result = self._get(f'/contacts/{contact_id}/appointments')
        return result.get('events', []) if result else []

    def validate_contact_location(self, contact_id):
        if self.client.is_demo:
            return True
        result = self._get(f'/contacts/{contact_id}')
        return bool(result and result.get('contact', {}).get('locationId') == self.client.highlevel_location_id)
