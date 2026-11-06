# python-app database-service

Toolchain: python-app
Profile: database-service

Create a service backed by PostgreSQL 17 inside the offline sandbox. Prove migration ordering, transaction rollback after a constraint violation, a real HTTP response and 404, and data persistence after a server restart. Use Unix sockets; no external database or Compose.
