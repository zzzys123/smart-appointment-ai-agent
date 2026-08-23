package com.smartappointment.appointment.booking.entity;

import com.smartappointment.appointment.technician.entity.Technician;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

import java.time.LocalDateTime;

@Entity
@Table(
        name = "appointment_slot",
        uniqueConstraints = @UniqueConstraint(
                name = "uk_technician_slot",
                columnNames = {"technician_id", "slot_start"}
        )
)
public class AppointmentSlot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "appointment_id", nullable = false)
    private Appointment appointment;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "technician_id", nullable = false)
    private Technician technician;

    @Column(name = "slot_start", nullable = false)
    private LocalDateTime slotStart;

    protected AppointmentSlot() {
    }

    public AppointmentSlot(
            Appointment appointment,
            Technician technician,
            LocalDateTime slotStart
    ) {
        this.appointment = appointment;
        this.technician = technician;
        this.slotStart = slotStart;
    }

    public Long getId() {
        return id;
    }

    public Appointment getAppointment() {
        return appointment;
    }

    public Technician getTechnician() {
        return technician;
    }

    public LocalDateTime getSlotStart() {
        return slotStart;
    }
}
