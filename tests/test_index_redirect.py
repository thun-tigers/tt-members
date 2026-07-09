def test_index_redirects_members_admins_to_member_list(client, app):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = User(
            auth_user_id=101,
            username='admin-user',
            first_name='Admin',
            last_name='User',
            display_name='Admin User',
            email='admin@example.com',
            platform_role='admin',
            service_role='user',
            profile_complete=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as session:
        session['user_id'] = user_id

    response = client.get('/', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/members')


def test_index_keeps_regular_users_on_profile_setup(client, app):
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = User(
            auth_user_id=102,
            username='regular-user',
            first_name='Regular',
            last_name='User',
            display_name='Regular User',
            email='regular@example.com',
            platform_role='user',
            service_role='user',
            profile_complete=False,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client.session_transaction() as session:
        session['user_id'] = user_id

    response = client.get('/', follow_redirects=False)

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/profile')
