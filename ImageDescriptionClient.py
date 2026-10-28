import argparse
import pathlib
import socket
from typing import Literal, Never, overload


MAX_MESSAGE_BYTES = 5 * 1024 * 1024


class ServerError(RuntimeError):
    """Exception raised for server-side errors."""
    pass


def create_connection(hostname: str, port: int) -> socket.socket:
    """Create a TCP connection to the specified hostname and port."""
    return socket.create_connection((hostname, port))


def recv_exact(conn: socket.socket, length: int) -> bytes:
    """
    Read exactly length bytes from the connection.

    Raises BrokenPipeError if the connection is closed before reading the required number of bytes.
    """
    buffer = b""
    while len(buffer) < length:
        chunk = conn.recv(length - len(buffer), socket.MSG_WAITALL)
        if not chunk:
            raise BrokenPipeError("Connection closed during a message")
        buffer += chunk
    return buffer


def read_input(prompt: object = "client: ") -> str:
    """
    Read input from the user with the given prompt.

    Returns an empty string if EOF is encountered.
    """
    try:
        return input(prompt)
    except EOFError:
        print()
        return ""


@overload
def read_message(conn: socket.socket, is_text: Literal[True] = True) -> str:
    pass
@overload
def read_message(conn: socket.socket, is_text: Literal[False]) -> bytes:
    pass
def read_message(conn: socket.socket, is_text: bool = True) -> str | bytes:
    """
    Read a message from the connection.

    Args:
        conn: The socket connection to read from.
        is_text: True if the message is expected to be text, False if binary.

    Returns:
        The message as a string if is_text is True, otherwise as bytes.

    Raises:
        ConnectionAbortedError: If the message length is malformed.
        BrokenPipeError: If the connection is closed before reading the full message.
        ServerError: If the message starts with "ERROR - ".
    """
    length_digits = recv_exact(conn, 10)
    
    try:
        length = int(length_digits.decode("ascii"))
    except ValueError:
        conn.close()
        raise ConnectionAbortedError("Received a malformed message")
    
    message = recv_exact(conn, length)
    if not is_text: return message

    message = message.decode()
    if message.startswith("ERROR - "): raise ServerError(message[8:])
    return message


def send_message(conn: socket.socket, payload: str | bytes) -> None:
    """
    Send a message to the connection.
    
    Raises ConnectionError if the send fails.
    """
    if isinstance(payload, str):
        payload = payload.encode()

    length = len(payload)
    length_digits = f"{length:010d}".encode("ascii")
    conn.send(length_digits)
    conn.send(payload)


def print_message(message: str, is_server: bool = True) -> None:
    """
    Print a message with a prefix indicating whether it is from the server or client.

    Args:
        message: The message to print.
        is_server: True if the message is from the server, False if from the client.
    """
    prefix = "server" if is_server else "client"
    for line in message.splitlines():
        print(f"{prefix}: {line}")


def command_upload(conn: socket.socket):
    """
    Handle the UPLOAD command.

    Prompts the user for a file path, reads the file, and sends it to the server.
    Raises RuntimeError if the file does not exist or is not a file.
    """
    file_path = pathlib.Path(read_input())
    if not file_path.exists() or not file_path.is_file():
        raise RuntimeError("The file does not exist")

    file_name = file_path.name
    if len(file_name) > 255:
        raise RuntimeError("File name exceeded the limit of 255 characters")

    file = file_path.read_bytes()
    if len(file) < 1 or len(file) > MAX_MESSAGE_BYTES:
        raise RuntimeError("File size must be between 1 byte to 5 MiB")

    send_message(conn, f"UPLOAD {file_name}")
    send_message(conn, file)

    print_message(read_message(conn))


def command_list(conn: socket.socket) -> None:
    """
    Handle the LIST command.

    Sends a LIST request to the server and prints the response.
    """
    send_message(conn, "LIST")
    print_message(read_message(conn))


def command_describe(conn: socket.socket) -> None:
    """
    Handle the DESCRIBE command.

    Prompts the user for an image ID, requests its description from the server,
    and prints the response.
    """
    image_id = read_input().strip()
    send_message(conn, f"DESCRIBE {image_id}")
    print_message(read_message(conn))


def command_download(conn: socket.socket) -> None:
    """
    Handle the DOWNLOAD command.

    Prompts the user for an image ID and a file path, requests the image from the server,
    and saves it to the specified file path.
    """
    image_id = read_input().strip()
    file_path = pathlib.Path(read_input().strip())
    if file_path.exists():
        raise RuntimeError("The file had already existed")
    file_path.parent.mkdir(parents=True, exist_ok=True)

    send_message(conn, f"DOWNLOAD {image_id}")
    print_message(read_message(conn))

    image = read_message(conn, is_text=False)
    file_path.write_bytes(image)
    print_message(f"Saved to {file_path}", is_server=False)


def command_remove(conn: socket.socket) -> None:
    """
    Handle the REMOVE command.

    Prompts the user for an image ID, sends a REMOVE request to the server,
    and prints the response.
    """
    image_id = read_input().strip()
    send_message(conn, f"REMOVE {image_id}")
    print_message(read_message(conn))


def command_quit(conn: socket.socket) -> Never:
    """
    Handle the QUIT command.

    Sends a QUIT request to the server, prints the response, closes the connection,
    and exits the program.
    """
    send_message(conn, "QUIT")
    print_message(read_message(conn))

    conn.close()
    exit(0)


def command_fallback(conn: socket.socket, command: str) -> None:
    """
    Handle any unrecognized command.

    Sends the command to the server and prints the response.
    """
    send_message(conn, command)
    print_message(read_message(conn))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("hostname")
    parser.add_argument("port", type=int)
    args = parser.parse_args()

    try:
        conn = create_connection(args.hostname, args.port)
        while True:
            command = read_input().strip().upper()
            if command == "": continue
            try:
                match command:
                    case "UPLOAD":
                        command_upload(conn)
                    case "LIST":
                        command_list(conn)
                    case "DESCRIBE":
                        command_describe(conn)
                    case "DOWNLOAD":
                        command_download(conn)
                    case "REMOVE":
                        command_remove(conn)
                    case "QUIT":
                        command_quit(conn)
                    case _:
                        command_fallback(conn, command)
            except ConnectionError:
                raise
            except KeyboardInterrupt:
                conn.close()
                exit(0)
            except ServerError as err:
                print_message(f"ERROR - {str(err)}")
            except Exception as err:
                print_message(f"ERROR - {str(err)}", is_server=False)
    except ConnectionError as err:
        print_message(f"ERROR - {str(err)}", is_server=False)


if __name__ == "__main__":
    main()
