package com.smartappointment.appointment.booking.exception;

public class AppointmentSlotConflictException extends RuntimeException {

    private static final String CODE = "APPOINTMENT_SLOT_CONFLICT";

    public AppointmentSlotConflictException(String message) {
        super(message);
    }

    public String getCode() {
        return CODE;
    }
}
