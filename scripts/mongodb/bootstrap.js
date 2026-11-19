const fs = require("fs");

const localUri =
  "mongodb://127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=5000";

const connections = [];
let stage = "READ_INPUT";

async function openConnection() {
  const connection = await new Mongo(localUri);
  connections.push(connection);
  return connection;
}

async function authenticate(connection, database, username, password) {
  const result = await connection.getDB(database).auth(username, password);

  if (result !== 1 && result?.ok !== 1) {
    const error = new Error("Authentication failed.");
    error.code = 18;
    throw error;
  }
}

function validateInput(input) {
  const namePattern = /^[a-z][a-z0-9_]{0,63}$/;
  const databasePattern = /^[a-z][a-z0-9_]{0,62}$/;

  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("Invalid input.");
  }

  for (const key of ["root_username", "app_username"]) {
    if (
      typeof input[key] !== "string" ||
      !namePattern.test(input[key])
    ) {
      throw new Error("Invalid username.");
    }
  }

  for (const key of ["root_password", "app_password"]) {
    if (
      typeof input[key] !== "string" ||
      input[key].length < 16 ||
      input[key].length > 128
    ) {
      throw new Error("Invalid password length.");
    }
  }

  if (
    typeof input.database !== "string" ||
    !databasePattern.test(input.database) ||
    ["admin", "config", "local"].includes(input.database)
  ) {
    throw new Error("Invalid application database.");
  }

  if (input.root_username === input.app_username) {
    throw new Error("Admin and application usernames must differ.");
  }
}

async function bootstrap(input) {
  validateInput(input);

  let rootConnection;
  let rootCreated = false;
  let appCreated = false;

  stage = "AUTHENTICATE_ROOT";

  try {
    rootConnection = await openConnection();

    await authenticate(
      rootConnection,
      "admin",
      input.root_username,
      input.root_password,
    );
  } catch {
    stage = "CREATE_FIRST_ROOT_USER";

    const initialConnection = await openConnection();

    await initialConnection.getDB("admin").createUser({
      user: input.root_username,
      pwd: input.root_password,
      roles: [{ role: "root", db: "admin" }],
      mechanisms: ["SCRAM-SHA-256"],
    });

    rootCreated = true;
    stage = "VERIFY_ROOT";

    rootConnection = await openConnection();

    await authenticate(
      rootConnection,
      "admin",
      input.root_username,
      input.root_password,
    );
  }

  stage = "CHECK_APPLICATION_USER";

  const applicationDatabase = rootConnection.getDB(input.database);
  const existingUser = await applicationDatabase.getUser(input.app_username);

  if (existingUser === null) {
    stage = "CREATE_APPLICATION_USER";

    await applicationDatabase.createUser({
      user: input.app_username,
      pwd: input.app_password,
      roles: [{ role: "readWrite", db: input.database }],
      mechanisms: ["SCRAM-SHA-256"],
    });

    appCreated = true;
  } else {
    const roles = existingUser.roles || [];

    if (
      roles.length !== 1 ||
      roles[0].role !== "readWrite" ||
      roles[0].db !== input.database
    ) {
      throw new Error("Existing application user has unexpected roles.");
    }
  }

  stage = "AUTHENTICATE_APPLICATION_USER";

  const appConnection = await openConnection();

  await authenticate(
    appConnection,
    input.database,
    input.app_username,
    input.app_password,
  );

  stage = "VERIFY_APPLICATION_READ_WRITE";

  const checks = appConnection
    .getDB(input.database)
    .getCollection("_bootstrap_checks");

  const probeId = new ObjectId();

  await checks.insertOne({
    _id: probeId,
    checked_at: new Date(),
  });

  try {
    const document = await checks.findOne({ _id: probeId });

    if (document === null) {
      throw new Error("Application read verification failed.");
    }
  } finally {
    await checks.deleteOne({ _id: probeId });
  }

  return {
    status: "ready",
    database: input.database,
    app_user: input.app_username,
    root_created: rootCreated,
    app_created: appCreated,
  };
}

(async () => {
  let report;
  let failed = false;

  try {
    const input = JSON.parse(fs.readFileSync(0, "utf8"));
    report = await bootstrap(input);
  } catch (error) {
    failed = true;

    const code = Number.isInteger(error.code)
      ? error.code
      : "unknown";

    console.error(`BOOTSTRAP_FAILED stage=${stage} code=${code}`);
  } finally {
    for (const connection of connections.reverse()) {
      try {
        await connection.close();
      } catch {
        // Closing a failed connection must not hide the bootstrap result.
      }
    }
  }

  if (failed) {
    quit(1);
  }

  print(JSON.stringify(report));
  quit(0);
})();