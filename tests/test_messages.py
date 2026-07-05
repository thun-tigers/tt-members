def test_internal_message_count_endpoint_returns_pending_count(client, app):
    from app.extensions import db
    from app.models import MemberProfile, User

    with app.app_context():
        user = User(
            auth_user_id=77,
            username='message-user',
            first_name='Message',
            last_name='User',
            display_name='Message User',
            email='message@example.com',
            platform_role='user',
            service_role='user',
            profile_complete=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(MemberProfile(user_id=user.id, first_name='Message', last_name='User', license_photo_status='pending'))
        db.session.commit()

    response = client.get(
        '/api/internal/messages/count',
        query_string={'auth_user_id': 77},
        headers={'X-TT-Internal-Secret': 'test-internal-secret'},
    )

    assert response.status_code == 200
    assert response.get_json()['pending_messages_count'] == 1
