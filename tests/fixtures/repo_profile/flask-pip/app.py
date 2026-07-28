from flask import Flask

app = Flask(__name__)


@app.route("/")
def index() -> str:
    return "hello"


if __name__ == "__main__":
    app.run()
