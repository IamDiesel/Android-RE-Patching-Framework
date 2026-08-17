# Virtuagym API Documentation

This documentation provides an overview of the Virtuagym API based on reverse-engineered network traffic. It covers the login process, fetching schedules, and managing event bookings.

## Base URLs
- **REST API:** `https://api.virtuagym.com`
- **IAM/Auth Service:** `https://iam.services.virtuagym.com`
- **Gateway Services:** `https://gateway.services.virtuagym.com`

## Authentication

The app uses two main methods for authentication depending on the endpoint.

### 1. Basic Authentication (Primary for API)
Most endpoints on `api.virtuagym.com` use standard HTTP Basic Authentication with the user's email and password.

**Header:**
```http
Authorization: Basic <base64(email:password)>
```

### 2. OAuth2 / OpenID Connect Token
For newer microservices (like `gateway.services.virtuagym.com`), the app requests an OAuth2 token using the Resource Owner Password Credentials grant.

**Endpoint:** `POST https://iam.services.virtuagym.com/auth/realms/virtuagym/protocol/openid-connect/token`

**Request Body (x-www-form-urlencoded):**
```http
username=user@example.com
&password=YourPassword!
&scope=schedule+fitzone+qrcode+profile+habits+training-sessions
&client_id=mobile-app-cma-1
&grant_type=password
```

**Response:**
Returns a JSON object containing `access_token`, `refresh_token`, and `expires_in`. This token is then used as a Bearer token: `Authorization: Bearer <access_token>`.

---

## Global Query Parameters
Most endpoints on the main API require an `api_key` and often `sync_from`.
- `api_key`: `a57ea7f01b21860d7b20986dd448dfbb04e42d795` (Appears to be a static client API key)
- `sync_from`: Unix timestamp (e.g., `1786928975`) or `0` for initial fetch.

---

## Endpoints

### 1. Get Current User Profile
**Endpoint:** `GET /api/v0/user/current?sync_from=0&app_id=digifit.virtuagym.client.android&app_version=12.4.2&api_key={api_key}`

**Description:** Retrieves the logged-in user's profile, including their `user_id` and associated `club_ids`.

**Response Excerpt:**
```json
{
  "statuscode": 200,
  "result": {
    "id": 25531011,
    "email": "user@example.com",
    "firstname": "First-Name",
    "club_ids": [34314]
  }
}
```

### 2. Get Schedule (Termine abrufen)
**Endpoint:** `GET /api/v0/club/{club_id}/event/{yyyy}/{mm}/{dd}/to/{yyyy}/{mm}/{dd}?api_key={api_key}`
*Example:* `/api/v0/club/34314/event/2026/08/17/to/2026/08/23`

**Description:** Fetches all events for a specific club within a date range.

**Response Data (Per Event):**
- `event_id`: Unique identifier for the appointment (e.g., `"278806781-6a576a513426b7-93254514"`)
- `event_start` / `event_end`: Unix timestamps for the event's duration.
- `attendees`: Current number of participants booked.
- `max_attendees`: Maximum allowed participants.
- `joinable`: `1` if you can book it, `0` otherwise.
- `joined`: `1` if the current user has booked it, `0` otherwise.
- `canceled`: `1` if the event is canceled.
- `cancel_before_duration`: Time in seconds before start when cancellation is no longer allowed (e.g., `86400` = 24 hours).

### 3. Get Event Details
**Endpoint:** `GET /api/v0/club/{club_id}/event/{event_id}/details?api_key={api_key}`

**Description:** Fetches detailed information about a specific event, including descriptions, instructors, and your current booking status.

**Response Excerpt:**
```json
{
  "result": {
    "activity_id": 315155,
    "attendees": 15,
    "max_attendees": 26,
    "class_name": "OPEN GYM",
    "is_bookable": true,
    "is_full": false,
    "cancelable": true,
    "cancel_time_msg": "Du kannst dieses Event bis zu 24 Stunden im Voraus stornieren"
  }
}
```

### 4. Book an Event (Termin buchen)
**Endpoint:** `PUT /api/club/{club_id}/event/{event_id}/join?api_key={api_key}`

**Headers:**
- `Content-Type: application/json`
- `Authorization: Basic <base64>`

**Request Body:**
```json
{
  "send_email": 1
}
```

**Response:**
```json
{
  "statuscode": 200,
  "result": {
    "result": "joined successfully"
  }
}
```

### 5. Cancel an Event (Termin stornieren)
**Endpoint:** `PUT /api/club/{club_id}/event/{event_id}/leave?api_key={api_key}`

**Headers:**
- `Content-Type: application/json`
- `Authorization: Basic <base64>`

**Request Body:**
```json
{
  "send_email": 1,
  "reason": "rescheduled"
}
```

**Response:**
```json
{
  "statuscode": 200,
  "result": {
    "result": "left successfully"
  }
}
```

### 6. Get User's Booked Events
**Endpoint:** `GET /api/v0/user/{user_id}/booked_club_events?max_results=500&sync_from={timestamp}&api_key={api_key}`

**Description:** Lists all events currently booked by the user. If an event is canceled by the user, the API will return the record with `"deleted": 1`.

**Response Excerpt:**
```json
{
  "result": [
    {
      "event_id": "278806781-6a576a513426b7-93254514",
      "club_id": 34314,
      "user_id": 25531011,
      "start": 1787067000,
      "end": 1787074200,
      "deleted": 0
    }
  ]
}
```
