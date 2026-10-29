import os

# Never pick up a developer's .env: it may point at a real database the tests would reset.
os.environ["FUZER_ENV_FILE"] = "none"
