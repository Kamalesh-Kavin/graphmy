// Sample Java source file for testing the Java parser.
// Exercises: classes, interfaces, enums, methods, constructors, extends, implements, imports.

package com.example;

import java.util.List;
import java.util.ArrayList;

/**
 * Interface that all animals must implement.
 */
interface Animal {
    String speak();
    String describe();
}

/**
 * Breed enumeration.
 */
enum Breed {
    LABRADOR,
    POODLE,
    HUSKY
}

/**
 * Base class for pets.
 */
class Pet {
    protected String name;
    protected int age;

    public Pet(String name, int age) {
        this.name = name;
        this.age = age;
    }

    public String getName() {
        return name;
    }
}

/**
 * Dog class extends Pet and implements Animal.
 */
public class Dog extends Pet implements Animal {
    private Breed breed;

    public Dog(String name, int age, Breed breed) {
        super(name, age);
        this.breed = breed;
    }

    @Override
    public String speak() {
        return name + " says: Woof!";
    }

    @Override
    public String describe() {
        return name + " (" + breed + ", age " + age + ")";
    }

    public String fetch(String item) {
        return greet(item);
    }

    /**
     * Returns a greeting string.
     */
    public static String greet(String name) {
        return "Hello, " + name + "!";
    }

    public static void main(String[] args) {
        Dog dog = new Dog("Rex", 3, Breed.LABRADOR);
        System.out.println(dog.speak());
        System.out.println(Dog.greet("world"));
        List<String> names = new ArrayList<>();
        names.add("Alice");
    }
}
