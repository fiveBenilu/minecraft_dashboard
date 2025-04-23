let currentPeriod = '24h';
let historyChart = null;

async function loadStats() {
    try {
        const res = await fetch('/systemdata');
        const data = await res.json();
        document.getElementById('cpu-value').innerText = data.cpu + '%';
        document.getElementById('ram-value').innerText = data.ram + '%';
        document.getElementById('disk-value').innerText = data.disk + '%';
    } catch (error) {
        console.error('Fehler beim Laden der Systemdaten:', error);
    }
}

async function loadUptime() {
    try {
        const res = await fetch('/uptime');
        const data = await res.json();
        document.getElementById('uptime').innerText = data.uptime;
    } catch (error) {
        console.error('Fehler beim Laden der Uptime:', error);
        document.getElementById('uptime').innerText = 'Unbekannt';
    }
}

async function loadHistoryData(period) {
    try {
        const res = await fetch(`/history/metrics/${period}`);
        const data = await res.json();
        updateHistoryChart(data.metrics);
        updateStatusEvents(data.status_changes);
    } catch (error) {
        console.error('Fehler beim Laden der Verlaufsdaten:', error);
    }
}

function updateHistoryChart(metrics) {
    const ctx = document.getElementById('historyChart').getContext('2d');
    const labels = metrics.map(entry => {
        const date = new Date(entry.timestamp);
        return date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) + 
               (currentPeriod !== '24h' ? ' ' + date.toLocaleDateString() : '');
    });

    const cpuData = metrics.map(entry => entry.cpu_usage);
    const ramData = metrics.map(entry => entry.ram_usage);
    const diskData = metrics.map(entry => entry.disk_usage);

    if (historyChart) {
        historyChart.data.labels = labels;
        historyChart.data.datasets[0].data = cpuData;
        historyChart.data.datasets[1].data = ramData;
        historyChart.data.datasets[2].data = diskData;
        historyChart.update();
    } else {
        historyChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'CPU %',
                        data: cpuData,
                        borderColor: 'rgba(13, 110, 253, 1)',
                        backgroundColor: 'rgba(13, 110, 253, 0.1)',
                        fill: true,
                        tension: 0.3
                    },
                    {
                        label: 'RAM %',
                        data: ramData,
                        borderColor: 'rgba(25, 135, 84, 1)',
                        backgroundColor: 'rgba(25, 135, 84, 0.1)',
                        fill: true,
                        tension: 0.3
                    },
                    {
                        label: 'Disk %',
                        data: diskData,
                        borderColor: 'rgba(255, 193, 7, 1)',
                        backgroundColor: 'rgba(255, 193, 7, 0.1)',
                        fill: true,
                        tension: 0.3
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 2.5,
                plugins: {
                    legend: {
                        position: 'top',
                        labels: {
                            color: 'rgba(255, 255, 255, 0.7)'
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.7)'
                        }
                    },
                    x: {
                        grid: {
                            color: 'rgba(255, 255, 255, 0.1)'
                        },
                        ticks: {
                            color: 'rgba(255, 255, 255, 0.7)',
                            maxRotation: 45,
                            minRotation: 45
                        }
                    }
                }
            }
        });
    }
}

function updateStatusEvents(statusChanges) {
    const container = document.getElementById('status-events');
    container.innerHTML = '';

    if (statusChanges.length === 0) {
        container.innerHTML = '<p class="text-center">Keine Status-Änderungen im ausgewählten Zeitraum.</p>';
        return;
    }

    statusChanges.reverse().slice(0, 10).forEach(event => {
        const date = new Date(event.timestamp);
        const formattedDate = date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
        const isOnline = event.status === 'Läuft';
        const statusClass = isOnline ? 'status-event-online' : 'status-event-offline';
        const statusIcon = isOnline ?
            '<i class="bi bi-check-circle-fill text-success"></i>' :
            '<i class="bi bi-x-circle-fill text-danger"></i>';

        const eventElement = document.createElement('div');
        eventElement.className = `status-event ${statusClass}`;
        eventElement.innerHTML = `${statusIcon} <strong>${event.status}</strong> - ${formattedDate}`;
        container.appendChild(eventElement);
    });
}

document.querySelectorAll('.period-btn').forEach(button => {
    button.addEventListener('click', function () {
        document.querySelectorAll('.period-btn').forEach(btn => btn.classList.remove('active'));
        this.classList.add('active');
        currentPeriod = this.getAttribute('data-period');
        loadHistoryData(currentPeriod);
    });
});

document.addEventListener('DOMContentLoaded', function () {
    loadStats();
    loadUptime();
    loadHistoryData(currentPeriod);
    setInterval(loadStats, 10000);
    setInterval(loadUptime, 30000);
    setInterval(() => loadHistoryData(currentPeriod), 3600000);
});