FROM freeradius/freeradius-server:latest

USER root
RUN apt-get update && apt-get install -y \
    freeradius-mysql \
    && rm -rf /var/lib/apt/lists/*

# Copy config files
COPY clients.conf /etc/freeradius/clients.conf
COPY users /etc/freeradius/users
COPY sql.conf /etc/freeradius/mods-available/sql

# Enable SQL module
RUN ln -sf /etc/freeradius/mods-available/sql \
           /etc/freeradius/mods-enabled/sql

# Enable sql in authorize section (remove the leading dash/minus)
RUN sed -i 's/\t-sql/\tsql/g' /etc/freeradius/sites-enabled/default

# Fix permissions
RUN chmod 640 /etc/freeradius/clients.conf && \
    chmod 640 /etc/freeradius/users && \
    chmod 640 /etc/freeradius/mods-available/sql && \
    chown freerad:freerad /etc/freeradius/clients.conf && \
    chown freerad:freerad /etc/freeradius/users && \
    chown freerad:freerad /etc/freeradius/mods-available/sql

USER freerad