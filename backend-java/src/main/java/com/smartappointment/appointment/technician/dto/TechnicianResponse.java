package com.smartappointment.appointment.technician.dto;

import com.smartappointment.appointment.technician.entity.Technician;

public record TechnicianResponse(
        long id,
        String name,
        String gender,
        String strength,
        boolean enabled
) {
    public static TechnicianResponse from(Technician technician) {
        return new TechnicianResponse(
                technician.getId(),
                technician.getName(),
                technician.getGender(),
                technician.getStrength(),
                technician.isEnabled()
        );
    }
}
