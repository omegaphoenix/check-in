from datetime import datetime

import pytest
from pytest_mock import MockerFixture

from lib.checkin_handler import CheckInHandler
from lib.checkin_scheduler import CheckInScheduler
from lib.config import AccountConfig, ReservationConfig
from lib.fare_checker import FareChecker
from lib.notification_handler import NotificationHandler
from lib.reservation_monitor import TOO_MANY_REQUESTS_CODE, AccountMonitor, ReservationMonitor
from lib.utils import FlightChangeError, LoginError, RequestError
from lib.webdriver import WebDriver

# This needs to be accessed to be tested
# pylint: disable=protected-access


def test_reservation_monitor_monitors(mocker: MockerFixture) -> None:
    mock_monitor = mocker.patch.object(ReservationMonitor, "_monitor")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.monitor()
    mock_monitor.assert_called_once()


def test_reservation_monitor_handles_keyboard_interrupt(mocker: MockerFixture) -> None:
    mocker.patch.object(ReservationMonitor, "_monitor", side_effect=KeyboardInterrupt)
    mock_stop_monitoring = mocker.patch.object(ReservationMonitor, "_stop_monitoring")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.monitor()
    mock_stop_monitoring.assert_called_once()


def test_reservation_monitor_monitors_reservations_continuously(mocker: MockerFixture) -> None:
    # Since the monitor function runs in an infinite loop, throw an Exception when the
    # sleep function is called a second time to break out of the loop.
    mocker.patch.object(ReservationMonitor, "_smart_sleep", side_effect=["", KeyboardInterrupt])
    mock_refresh_headers = mocker.patch.object(CheckInScheduler, "refresh_headers")
    mock_schedule_reservations = mocker.patch.object(ReservationMonitor, "_schedule_reservations")
    mock_check_flight_fares = mocker.patch.object(ReservationMonitor, "_check_flight_fares")

    config = ReservationConfig()
    config.confirmation_number = "test_num"
    test_monitor = ReservationMonitor(config)
    test_monitor.checkin_scheduler.flights = ["test_flight"]

    with pytest.raises(KeyboardInterrupt):
        test_monitor._monitor()

    assert mock_refresh_headers.call_count == 2
    assert mock_schedule_reservations.call_count == 2
    mock_schedule_reservations.assert_called_with(
        [{"confirmationNumber": config.confirmation_number}]
    )
    assert mock_check_flight_fares.call_count == 2


def test_reservation_monitor_stops_monitoring_when_no_flights_are_scheduled(
    mocker: MockerFixture,
) -> None:
    mock_smart_sleep = mocker.patch.object(ReservationMonitor, "_smart_sleep")
    mock_refresh_headers = mocker.patch.object(CheckInScheduler, "refresh_headers")
    mock_schedule_reservations = mocker.patch.object(ReservationMonitor, "_schedule_reservations")
    mock_check_flight_fares = mocker.patch.object(ReservationMonitor, "_check_flight_fares")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor._monitor()

    mock_refresh_headers.assert_called_once()
    mock_schedule_reservations.assert_called_once()
    mock_check_flight_fares.assert_not_called()
    mock_smart_sleep.assert_not_called()


def test_reservation_monitor_monitors_reservations_once_if_retrieval_interval_is_zero(
    mocker: MockerFixture,
) -> None:
    mock_smart_sleep = mocker.patch.object(ReservationMonitor, "_smart_sleep")
    mock_refresh_headers = mocker.patch.object(CheckInScheduler, "refresh_headers")
    mock_schedule_reservations = mocker.patch.object(ReservationMonitor, "_schedule_reservations")
    mock_check_flight_fares = mocker.patch.object(ReservationMonitor, "_check_flight_fares")

    config = ReservationConfig()
    config.retrieval_interval = 0
    test_monitor = ReservationMonitor(config)
    test_monitor.checkin_scheduler.flights = ["test_flight"]

    test_monitor._monitor()

    mock_refresh_headers.assert_called_once()
    mock_schedule_reservations.assert_called_once()
    mock_check_flight_fares.assert_called_once()
    mock_smart_sleep.assert_not_called()


def test_reservation_monitor_schedules_reservations_correctly(mocker: MockerFixture) -> None:
    mock_process_reservations = mocker.patch.object(CheckInScheduler, "process_reservations")
    reservations = [{"confirmationNumber": "Test1"}, {"confirmationNumber": "Test2"}]

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor._schedule_reservations(reservations)

    mock_process_reservations.assert_called_once_with(["Test1", "Test2"])


def test_reservation_monitor_does_not_check_fares_if_configuration_is_false(
    mocker: MockerFixture,
) -> None:
    mock_fare_checker = mocker.patch("lib.reservation_monitor.FareChecker")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.config.check_fares = False
    test_monitor._check_flight_fares()

    mock_fare_checker.assert_not_called()


def test_reservation_monitor_checks_fares_on_all_flights(mocker: MockerFixture) -> None:
    mock_check_flight_price = mocker.patch.object(FareChecker, "check_flight_price")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.config.check_fares = True
    test_monitor.checkin_scheduler.flights = ["test_flight1", "test_flight2"]
    test_monitor._check_flight_fares()

    assert mock_check_flight_price.call_count == len(test_monitor.checkin_scheduler.flights)


@pytest.mark.parametrize("exception", [RequestError("", ""), FlightChangeError, Exception])
def test_reservation_monitor_catches_error_when_checking_fares(
    mocker: MockerFixture, exception: Exception
) -> None:
    mock_check_flight_price = mocker.patch.object(
        FareChecker, "check_flight_price", side_effect=["", exception]
    )

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.config.check_fares = True
    test_monitor.checkin_scheduler.flights = ["test_flight1", "test_flight2"]
    test_monitor._check_flight_fares()

    assert mock_check_flight_price.call_count == len(test_monitor.checkin_scheduler.flights)


def test_reservation_monitor_smart_sleep_sleeps_for_correct_time(mocker: MockerFixture) -> None:
    mock_sleep = mocker.patch("time.sleep")
    mock_datetime = mocker.patch("lib.reservation_monitor.datetime")
    mock_datetime.utcnow.return_value = datetime(1999, 12, 31)

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.config.retrieval_interval = 24 * 60 * 60
    test_monitor._smart_sleep(datetime(1999, 12, 30, 12))

    mock_sleep.assert_called_once_with(12 * 60 * 60)


def test_reservation_monitor_wait_to_stop_waits_for_correct_time(mocker: MockerFixture) -> None:
    mock_process = mocker.patch("multiprocessing.Process")
    mock_process.name = "Process-10"

    mocker.patch("multiprocessing.current_process", return_value=mock_process)
    mock_sleep = mocker.patch("time.sleep")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor._wait_to_stop()

    mock_sleep.assert_called_once_with(1)


def test_reservation_monitor_stop_checkins_stops_all_checkins(mocker: MockerFixture) -> None:
    mock_checkin_handler = mocker.patch.object(CheckInHandler, "stop_check_in")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor.checkin_scheduler.checkin_handlers = [mock_checkin_handler] * 2
    test_monitor._stop_checkins()

    assert mock_checkin_handler.stop_check_in.call_count == 2


def test_reservation_monitor_stop_monitoring_stops_checkins(mocker: MockerFixture) -> None:
    mock_wait_to_stop = mocker.patch.object(ReservationMonitor, "_wait_to_stop")
    mock_stop_checkins = mocker.patch.object(ReservationMonitor, "_stop_checkins")

    test_monitor = ReservationMonitor(ReservationConfig())
    test_monitor._stop_monitoring()

    mock_wait_to_stop.assert_called_once()
    mock_stop_checkins.assert_called_once()


def test_account_monitor_monitors_the_account_continuously(mocker: MockerFixture) -> None:
    # Since the monitor function runs in an infinite loop, throw an Exception
    # when the sleep function is called a second time to break out of the loop.
    mocker.patch.object(ReservationMonitor, "_smart_sleep", side_effect=["", KeyboardInterrupt])
    mock_get_reservations = mocker.patch.object(
        AccountMonitor, "_get_reservations", return_value=([], False)
    )
    mock_schedule_reservations = mocker.patch.object(AccountMonitor, "_schedule_reservations")
    mock_check_flight_fares = mocker.patch.object(AccountMonitor, "_check_flight_fares")

    test_monitor = AccountMonitor(AccountConfig())

    with pytest.raises(KeyboardInterrupt):
        test_monitor._monitor()

    assert mock_get_reservations.call_count == 2
    assert mock_schedule_reservations.call_count == 2
    assert mock_check_flight_fares.call_count == 2


def test_account_monitor_skips_scheduling_on_too_many_requests_error(mocker: MockerFixture) -> None:
    # Since the monitor function runs in an infinite loop, throw an Exception
    # when the sleep function is called a second time to break out of the loop.
    mocker.patch.object(ReservationMonitor, "_smart_sleep", side_effect=[KeyboardInterrupt])
    mock_get_reservations = mocker.patch.object(
        AccountMonitor, "_get_reservations", return_value=([], True)
    )
    mock_schedule_reservations = mocker.patch.object(AccountMonitor, "_schedule_reservations")
    mock_check_flight_fares = mocker.patch.object(AccountMonitor, "_check_flight_fares")

    test_monitor = AccountMonitor(AccountConfig())

    with pytest.raises(KeyboardInterrupt):
        test_monitor._monitor()

    assert mock_get_reservations.call_count == 1
    assert mock_schedule_reservations.call_count == 0
    assert mock_check_flight_fares.call_count == 0


def test_account_monitor_checks_reservations_once_if_retrieval_interval_is_zero(
    mocker: MockerFixture,
) -> None:
    mock_smart_sleep = mocker.patch.object(AccountMonitor, "_smart_sleep")
    mock_get_reservations = mocker.patch.object(
        AccountMonitor, "_get_reservations", return_value=([], False)
    )
    mock_schedule_reservations = mocker.patch.object(AccountMonitor, "_schedule_reservations")
    mock_check_flight_fares = mocker.patch.object(AccountMonitor, "_check_flight_fares")

    config = AccountConfig()
    config.retrieval_interval = 0
    test_monitor = AccountMonitor(config)

    test_monitor._monitor()

    mock_smart_sleep.assert_not_called()
    mock_get_reservations.assert_called_once()
    mock_schedule_reservations.assert_called_once()
    mock_check_flight_fares.assert_called_once()


def test_get_reservations_skips_retrieval_on_too_many_requests_error(mocker: MockerFixture) -> None:
    mocker.patch.object(
        WebDriver, "get_reservations", side_effect=LoginError("", TOO_MANY_REQUESTS_CODE)
    )
    test_monitor = AccountMonitor(AccountConfig())
    reservations, skip_scheduling = test_monitor._get_reservations()
    assert len(reservations) == 0
    assert skip_scheduling


def test_get_reservations_exits_on_login_error(mocker: MockerFixture) -> None:
    mocker.patch.object(WebDriver, "get_reservations", side_effect=LoginError("", 400))
    mock_failed_login = mocker.patch.object(NotificationHandler, "failed_login")

    with pytest.raises(SystemExit):
        test_monitor = AccountMonitor(AccountConfig())
        test_monitor._get_reservations()

    mock_failed_login.assert_called_once()


def test_get_reservations_returns_the_correct_reservations(mocker: MockerFixture) -> None:
    reservations = [{"reservation1": "test1"}, {"reservation2": "test2"}]
    mocker.patch.object(WebDriver, "get_reservations", return_value=reservations)

    test_monitor = AccountMonitor(AccountConfig())
    new_reservations, skip_scheduling = test_monitor._get_reservations()

    assert new_reservations == reservations
    assert not skip_scheduling


def test_account_monitor_stop_monitoring_stops_checkins(mocker: MockerFixture) -> None:
    mock_wait_to_stop = mocker.patch.object(ReservationMonitor, "_wait_to_stop")
    mock_stop_checkins = mocker.patch.object(ReservationMonitor, "_stop_checkins")

    test_monitor = AccountMonitor(AccountConfig())
    test_monitor._stop_monitoring()

    mock_wait_to_stop.assert_called_once()
    mock_stop_checkins.assert_called_once()
