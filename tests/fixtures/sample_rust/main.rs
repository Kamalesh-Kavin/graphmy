// Sample Rust source file for testing the Rust parser.
// Exercises: functions, structs, enums, traits, impl blocks, use statements.

use std::fmt;

/// A trait that all animals must implement.
trait Animal {
    fn speak(&self) -> String;
    fn describe(&self) -> String;
}

/// Represents the breed of a dog.
#[derive(Debug)]
enum Breed {
    Labrador,
    Poodle,
    Husky,
}

impl fmt::Display for Breed {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let name = match self {
            Breed::Labrador => "Labrador",
            Breed::Poodle => "Poodle",
            Breed::Husky => "Husky",
        };
        write!(f, "{}", name)
    }
}

/// A dog struct.
#[derive(Debug)]
struct Dog {
    name: String,
    age: u8,
    breed: Breed,
}

impl Dog {
    /// Create a new Dog.
    fn new(name: &str, age: u8, breed: Breed) -> Self {
        Dog {
            name: name.to_string(),
            age,
            breed,
        }
    }
}

impl Animal for Dog {
    fn speak(&self) -> String {
        format!("{} says: Woof!", self.name)
    }

    fn describe(&self) -> String {
        format!("{} ({}, age {})", self.name, self.breed, self.age)
    }
}

/// Returns a greeting message.
fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}

fn main() {
    let dog = Dog::new("Rex", 3, Breed::Labrador);
    println!("{}", dog.speak());
    println!("{}", greet("world"));
}
