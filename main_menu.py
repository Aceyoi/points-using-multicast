import socket
import threading
import pickle
import pygame
import sys
import copy
import subprocess

# ---- Настройки ----
BOARD_SIZE = 9
CELL_SIZE = 60
MARGIN = 40
SCREEN_SIZE = BOARD_SIZE * CELL_SIZE + MARGIN * 2
PORT = 50008
HOST = '0.0.0.0'

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BG_COLOR = (240, 200, 120)

pygame.init()
screen = pygame.display.set_mode((SCREEN_SIZE, SCREEN_SIZE))
pygame.display.set_caption("Точки multicast")
font = pygame.font.SysFont(None, 30)

# ---- Общие переменные ----
board = [[None for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
score = {'B': 0, 'W': 0}
current_player = 'B'
player_role = None  # 'host', 'player', 'spectator'
my_color = None     # 'B' или 'W' для игроков
clients = []
clients_lock = threading.Lock()
game_over = False
winner = None
restart_timer = None
RESTART_DELAY = 5  # секунды

# ---- Отрисовка ----
def draw_board():
    screen.fill(BG_COLOR)
    for i in range(BOARD_SIZE):
        pygame.draw.line(screen, BLACK,
                         (MARGIN + i * CELL_SIZE, MARGIN),
                         (MARGIN + i * CELL_SIZE, SCREEN_SIZE - MARGIN))
        pygame.draw.line(screen, BLACK,
                         (MARGIN, MARGIN + i * CELL_SIZE),
                         (SCREEN_SIZE - MARGIN, MARGIN + i * CELL_SIZE))
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] is not None:
                color = BLACK if board[y][x] == 'B' else WHITE
                pygame.draw.circle(screen, color,
                                   (MARGIN + x * CELL_SIZE,
                                    MARGIN + y * CELL_SIZE), CELL_SIZE // 2 - 4)

    text = font.render(f"Счёт: Чёрные {score['B']} — Белые {score['W']} | Ход: {'Чёрные' if current_player == 'B' else 'Белые'}", True, BLACK)
    screen.blit(text, (10, 10))
    pygame.display.flip()

def get_cell(pos):
    x, y = pos
    col = (x - MARGIN + CELL_SIZE // 2) // CELL_SIZE
    row = (y - MARGIN + CELL_SIZE // 2) // CELL_SIZE
    if 0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE:
        return row, col
    return None

# ---- Логика Го ----
def get_neighbors(r, c):
    for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
        nr, nc = r+dy, c+dx
        if 0 <= nr < BOARD_SIZE and 0 <= nc < BOARD_SIZE:
            yield nr, nc

def get_group(r, c, color):
    group, queue = set(), [(r, c)]
    while queue:
        y, x = queue.pop()
        if (y, x) not in group:
            group.add((y, x))
            for ny, nx in get_neighbors(y, x):
                if board[ny][nx] == color:
                    queue.append((ny, nx))
    return group

def has_liberty(group):
    for y, x in group:
        for ny, nx in get_neighbors(y, x):
            if board[ny][nx] is None:
                return True
    return False

def remove_group(group):
    for y, x in group:
        board[y][x] = None

def try_capture(color):
    total = 0
    visited = set()
    for y in range(BOARD_SIZE):
        for x in range(BOARD_SIZE):
            if board[y][x] == color and (y, x) not in visited:
                group = get_group(y, x, color)
                visited |= group
                if not has_liberty(group):
                    remove_group(group)
                    total += len(group)
    return total

# ---- Сетевая часть ----
def broadcast_game_state(state):
    data = pickle.dumps(state)
    with clients_lock:
        for client in list(clients):
            try:
                client.sendall(data)
            except:
                print("Клиент отключён при отправке")
                if client in clients:
                    clients.remove(client)


def handle_client(conn, addr):
    global board, score, current_player, game_over, winner, restart_timer
    print(f"Клиент {addr} подключен.")

    # Добавляем клиента сразу
    with clients_lock:
        clients.append(conn)

    # Подсчёт игроков
    with clients_lock:
        player_count = sum(1 for c in clients if c.fileno() != -1)


    # Назначение цвета
    assigned_role = 'spectator'
    assigned_color = None

    if player_count <= 2:
        if player_count == 1:
            assigned_role = 'player'
            assigned_color = 'B'
        elif player_count == 2:
            assigned_role = 'player'
            assigned_color = 'W'

    init_data = {
        'board': board,
        'score': score,
        'turn': current_player,
        'role': assigned_role,
        'color': assigned_color
    }
    try:
        conn.sendall(pickle.dumps(init_data))
    except:
        print("Ошибка отправки начальных данных")
        return

    while True:
        try:
            data = conn.recv(4096)
            if not data:
                break
            move = pickle.loads(data)
            row, col, color = move['move']
            if board[row][col] is None and current_player == color:
                board[row][col] = color
                captured = try_capture('B' if color == 'W' else 'W')
                score[color] += captured
                current_player = 'W' if current_player == 'B' else 'B'

                global game_over, winner
                if score[color] >= 10:
                    game_over = True
                    winner = color

                    # Останавливаем предыдущий таймер, если был
                    if restart_timer:
                        restart_timer.cancel()

                    # Запускаем таймер для перезапуска
                    restart_timer = threading.Timer(RESTART_DELAY, reset_game)
                    restart_timer.start()

                broadcast_game_state({
                    'board': copy.deepcopy(board),
                    'score': copy.copy(score),
                    'turn': current_player,
                    'game_over': game_over,
                    'winner': winner
                })
        except:
            break

    print(f"Клиент {addr} отключён.")
    with clients_lock:
        if conn in clients:
            clients.remove(conn)
    conn.close()

def run_server():
    #SERVEO_CMD = f"ssh -T -R 50007:localhost:50007 serveo.net"
    #subprocess.Popen(SERVEO_CMD, shell=True)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen()
    print("Сервер запущен. Ожидание подключений...")
    threading.Thread(target=accept_loop, args=(server,), daemon=True).start()

def accept_loop(server):
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_client, args=(conn, addr), daemon=True).start()

def run_client():
    global board, score, current_player, player_role, my_color

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(('serveo.net', PORT))
    print("Подключено к серверу")

    def listen():
        global board, score, current_player, player_role, my_color, game_over, winner
        nonlocal client
        while True:
            try:
                data = client.recv(4096)
                if not data:
                    break
                update = pickle.loads(data)
                if 'role' in update:
                    player_role = update['role']
                    my_color = update['color']
                    print(f"Ваша роль: {player_role}, цвет: {my_color}")

                # Обновление состояния доски и хода — всегда
                if 'board' in update:
                    board[:] = update['board']
                if 'score' in update:
                    score.update(update['score'])
                if 'turn' in update:
                    current_player = update['turn']
                if 'game_over' in update:
                    game_over = update['game_over']
                if 'winner' in update:
                    winner = update['winner']
            except:
                break

    threading.Thread(target=listen, daemon=True).start()

    clock = pygame.time.Clock()
    while True:
        draw_board()
        if game_over:
            show_victory_message(winner)
            continue
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if my_color == current_player:
                    cell = get_cell(event.pos)
                    if cell and board[cell[0]][cell[1]] is None:
                        client.sendall(pickle.dumps({'move': (cell[0], cell[1], my_color)}))
        clock.tick(30)

def show_role_selection_screen():
    screen.fill(BG_COLOR)
    options = [("Хост (Чёрные)", 'host'), ("Игрок (Белые)", 'player'), ("Зритель", 'spectator')]
    buttons = []

    for i, (text, role) in enumerate(options):
        rect = pygame.Rect(SCREEN_SIZE//2 - 100, 150 + i*80, 200, 50)
        pygame.draw.rect(screen, WHITE, rect)
        pygame.draw.rect(screen, BLACK, rect, 2)
        label = font.render(text, True, BLACK)
        screen.blit(label, (rect.x + 10, rect.y + 10))
        buttons.append((rect, role))

    pygame.display.flip()
    return buttons

def start_serveo():
    SERVEO_CMD = f"ssh -T -R {PORT}:localhost:{PORT} serveo.net"
    subprocess.Popen(SERVEO_CMD, shell=True)

def show_victory_message(winner_color):
    text = "Чёрные выиграли!" if winner_color == 'B' else "Белые выиграли!"
    message = font.render(text, True, BLACK, WHITE)
    screen.blit(message, (SCREEN_SIZE // 2 - message.get_width() // 2, SCREEN_SIZE // 2))
    pygame.display.flip()

def reset_game():
    global board, score, current_player, game_over, winner
    board = [[None for _ in range(BOARD_SIZE)] for _ in range(BOARD_SIZE)]
    score = {'B': 0, 'W': 0}
    current_player = 'B'
    game_over = False
    winner = None
    broadcast_game_state({
        'board': copy.deepcopy(board),
        'score': copy.copy(score),
        'turn': current_player,
        'game_over': game_over,
        'winner': winner
    })
    print("Игра сброшена")


# ---- Главный запуск ----
if __name__ == '__main__':
    clock = pygame.time.Clock()
    selected = None
    buttons = show_role_selection_screen()

    while not selected:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            elif event.type == pygame.MOUSEBUTTONDOWN:
                pos = event.pos
                for rect, role in buttons:
                    if rect.collidepoint(pos):
                        selected = role
                        break
        clock.tick(30)

    if selected == 'host':
        player_role = 'host'
        my_color = 'B'
        run_server()
        run_client()
    elif selected == 'player':
        player_role = 'player'
        my_color = 'W'
        run_client()
    elif selected == 'spectator':
        player_role = 'spectator'
        run_client()