# Stage 1: Build the application using Maven
FROM maven:3.9.6-eclipse-temurin-17 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline
COPY src ./src
RUN mvn clean package -DskipTests

# Stage 2: Run the application
FROM eclipse-temurin:17-jdk-alpine
WORKDIR /app
EXPOSE 7080
EXPOSE 7081
COPY --from=build /app/target/dukascopy-api-websocket-1.0.war dukascopy-api-websocket.war
ENTRYPOINT ["java","-jar","dukascopy-api-websocket.war"]
