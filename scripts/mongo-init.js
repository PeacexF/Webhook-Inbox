// Runs once, as root, the first time an empty data directory is initialised.
// Creates the account the application actually connects with: readWrite on its own
// database and nothing else, so a compromised app cannot touch admin or other databases.
const database = process.env.MONGO_INITDB_DATABASE || "webhook_inbox";
const username = process.env.MONGO_APP_USERNAME;
const password = process.env.MONGO_APP_PASSWORD;

if (!username || !password) {
  print("mongo-init: MONGO_APP_USERNAME/MONGO_APP_PASSWORD unset, no app user created");
} else {
  db.getSiblingDB(database).createUser({
    user: username,
    pwd: password,
    roles: [{ role: "readWrite", db: database }],
  });
  print(`mongo-init: created '${username}' with readWrite on '${database}' only`);
}
