# Jobber Langflow Components Guide

## API Fundamentals

**API Type:** GraphQL (all requests are POST)

**Endpoint:** `https://api.getjobber.com/api/graphql`

**Required Headers (all requests):**
```
Authorization: Bearer {access_token}
Content-Type: application/json
```

**Developer Center:** https://developer.getjobber.com/docs/

---

## Authentication (OAuth 2.0)

Jobber uses OAuth 2.0. You need four credentials from the Developer Center:

| Credential | Description |
|---|---|
| `client_id` | Public app identifier |
| `client_secret` | Confidential app secret |
| `access_token` | Short-lived token (expires in 60 min) |
| `refresh_token` | Long-lived token to get new access tokens |

### Get a new access token using refresh token
```
POST https://api.getjobber.com/api/oauth/token
Content-Type: application/json
```
Body:
```json
{
  "client_id": "your_client_id",
  "client_secret": "your_client_secret",
  "grant_type": "refresh_token",
  "refresh_token": "your_refresh_token"
}
```
Response:
```json
{
  "access_token": "new_token",
  "refresh_token": "new_refresh_token",
  "token_type": "bearer",
  "expires_in": 3600
}
```

> **Note:** The refresh token also rotates — always store the new refresh token from each response.

### Initial Authorization Code Flow
```
POST https://api.getjobber.com/api/oauth/token
```
Body:
```json
{
  "client_id": "your_client_id",
  "client_secret": "your_client_secret",
  "grant_type": "authorization_code",
  "code": "auth_code_from_redirect"
}
```

---

## Making GraphQL Requests

All requests are `POST` to `https://api.getjobber.com/api/graphql` with a JSON body:
```json
{ "query": "{ ... }" }
```
Or with variables:
```json
{
  "query": "query GetClient($id: ID!) { client(id: $id) { ... } }",
  "variables": { "id": "abc123" }
}
```

---

## Common Queries

### Get Clients
```graphql
{
  clients {
    nodes {
      id
      name
      email
      phone
      billingAddress { street city province postalCode }
    }
  }
}
```

### Search Client by Email / Phone
```graphql
{
  clients(filter: { emails: { contains: "user@example.com" } }) {
    nodes {
      id
      name
      email
    }
  }
}
```

### Get Jobs for a Client
```graphql
query GetJobs($clientId: ID!) {
  jobs(filter: { clientId: $clientId }) {
    nodes {
      id
      title
      jobStatus
      startAt
      endAt
      invoiceStatus
      total
    }
  }
}
```

### Get Invoices
```graphql
{
  invoices {
    nodes {
      id
      invoiceNumber
      invoiceStatus
      subject
      total
      depositAmount
      issuedDate
      dueDate
      client { id name }
    }
  }
}
```

### Get Quotes
```graphql
{
  quotes {
    nodes {
      id
      quoteNumber
      quoteStatus
      title
      total
      client { id name }
    }
  }
}
```

### Get Upcoming Visits
```graphql
{
  visits(filter: { startAt: { after: "2024-01-01T00:00:00Z" } }) {
    nodes {
      id
      title
      startAt
      endAt
      visitStatus
      client { id name }
      job { id title }
    }
  }
}
```

### Create a Client
```graphql
mutation CreateClient($input: ClientCreateInput!) {
  clientCreate(input: $input) {
    client {
      id
      name
      email
    }
    userErrors { message path }
  }
}
```
Variables:
```json
{
  "input": {
    "firstName": "John",
    "lastName": "Doe",
    "emails": [{ "address": "john@example.com", "primary": true }],
    "phones": [{ "number": "+12345678900", "primary": true }]
  }
}
```

---

## Common Response Patterns

### Success
```json
{
  "data": {
    "clients": {
      "nodes": [ { "id": "...", "name": "..." } ]
    }
  }
}
```

### Error
```json
{
  "errors": [
    { "message": "Unauthorized", "locations": [...], "path": [...] }
  ]
}
```

### Mutation with userErrors
```json
{
  "data": {
    "clientCreate": {
      "client": { "id": "...", "name": "..." },
      "userErrors": []
    }
  }
}
```

---

## Planned Components

| Component | Query/Mutation | Purpose |
|-----------|---------------|---------|
| Jobber API (base) | Any GraphQL | Execute any query/mutation |
| Client Lookup | `clients(filter: {...})` | Find client by email or phone |
| Get Jobs | `jobs(filter: { clientId })` | Fetch jobs for a client |
| Get Invoices | `invoices(filter: { clientId })` | Fetch invoices for a client |
| Get Quotes | `quotes(filter: { clientId })` | Fetch quotes for a client |
| Get Visits | `visits(filter: { startAt })` | Fetch upcoming visits |
| Create Client | `clientCreate` | Add a new client |

---

## Tips

- **Testing:** Use GraphiQL in the Developer Center (three dots next to your app → Test in GraphiQL)
- **Token rotation:** Always save the new `refresh_token` returned from each token refresh
- **Pagination:** Jobber uses cursor-based pagination — use `pageInfo { hasNextPage endCursor }` and pass `after: $cursor`
- **IDs:** Jobber IDs are base64-encoded strings, e.g. `Q2xpZW50OjEyMzQ=`
- **As of Apr 2, 2024:** Only `application/json` is accepted — no form-encoded requests
