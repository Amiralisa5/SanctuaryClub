from .conftest import login, make_client, make_coach


def test_login_success_redirects_to_role_home(client_http):
    response = client_http.post(
        "/login", data={"email": "admin@test.local", "password": "admin-secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_login_wrong_password_rejected(client_http):
    response = client_http.post(
        "/login", data={"email": "admin@test.local", "password": "nope"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login"
    # Still anonymous: protected page redirects to login
    page = client_http.get("/admin", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"


def test_client_cannot_access_admin_or_coach_pages(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "client@test.local", "client-secret")
    assert client_http.get("/admin", follow_redirects=False).status_code == 403
    assert client_http.get("/coach", follow_redirects=False).status_code == 403
    assert client_http.get("/client").status_code == 200


def test_coach_cannot_view_another_coachs_client(client_http, db):
    coach_a = make_coach(db, email="a@test.local", name="Coach A")
    coach_b = make_coach(db, email="b@test.local", name="Coach B")
    client_of_b = make_client(db, coach_b)
    login(client_http, "a@test.local", "coach-secret")
    response = client_http.get(f"/coach/clients/{client_of_b.id}", follow_redirects=False)
    assert response.status_code == 403


def test_deactivated_user_cannot_log_in(client_http, db):
    coach = make_coach(db)
    coach.user.is_active = False
    db.commit()
    response = client_http.post(
        "/login", data={"email": "coach@test.local", "password": "coach-secret"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/login"
